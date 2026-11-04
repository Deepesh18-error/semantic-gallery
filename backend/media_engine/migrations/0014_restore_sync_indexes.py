from importlib import import_module
from django.db import migrations
from django.db.models import F


def backfill(apps, schema_editor):
    File = apps.get_model("media_engine", "CatalogFile")
    File.objects.filter(processing_status__in=["EMBEDDED", "PARTIAL"]).update(published_version_id=F("current_version_id"))
    revision = import_module("media_engine.migrations.0012_search_revision")
    with schema_editor.connection.cursor() as cursor:
        for table in revision.TABLES:
            for operation in ("INSERT", "UPDATE", "DELETE"):
                cursor.execute(f"DROP TRIGGER IF EXISTS search_revision_{table}_{operation}")
                condition = (" WHEN old.root_path != new.root_path OR old.paused != new.paused OR old.state != new.state"
                             if table == "catalogsource" and operation == "UPDATE" else "")
                cursor.execute(f"CREATE TRIGGER search_revision_{table}_{operation} AFTER {operation} "
                               f"ON media_engine_{table}{condition} BEGIN UPDATE catalog_search_revision "
                               "SET revision=revision+1 WHERE id=1; END")
        cursor.execute("UPDATE catalog_search_revision SET revision=revision+1 WHERE id=1")


fts_sql = import_module("media_engine.migrations.0011_search_evidence_fts").Migration.operations[0].sql


class Migration(migrations.Migration):
    dependencies = [("media_engine", "0013_catalogfile_file_identity_and_more")]
    operations = [migrations.RunSQL("""
        DROP TRIGGER IF EXISTS catalog_search_filename_au;
        DROP TRIGGER IF EXISTS catalog_search_au;
        DROP TRIGGER IF EXISTS catalog_search_ad;
        DROP TRIGGER IF EXISTS catalog_search_ai;
        DROP TABLE IF EXISTS catalog_search_fts;
        """ + fts_sql, reverse_sql=migrations.RunSQL.noop),
        migrations.RunPython(backfill, migrations.RunPython.noop)]
