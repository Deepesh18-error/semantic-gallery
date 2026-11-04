from django.db import migrations

TABLES = ("catalogfile", "catalogfileversion", "catalogsegment", "catalogvectorrecord",
          "catalogmembership", "catalogcollection", "catalogsource")


def install(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("CREATE TABLE catalog_search_revision (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL)")
        cursor.execute("INSERT INTO catalog_search_revision VALUES (1, 0)")
        for table in TABLES:
            for operation in ("INSERT", "UPDATE", "DELETE"):
                cursor.execute(f"CREATE TRIGGER search_revision_{table}_{operation} AFTER {operation} "
                               f"ON media_engine_{table} BEGIN UPDATE catalog_search_revision "
                               "SET revision=revision+1 WHERE id=1; END")


def uninstall(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            for operation in ("INSERT", "UPDATE", "DELETE"):
                cursor.execute(f"DROP TRIGGER IF EXISTS search_revision_{table}_{operation}")
        cursor.execute("DROP TABLE catalog_search_revision")


class Migration(migrations.Migration):
    dependencies = [("media_engine", "0011_search_evidence_fts")]
    operations = [migrations.RunPython(install, uninstall)]
