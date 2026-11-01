from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("media_engine", "0008_catalogfileversion_extraction_coverage_and_more")]
    operations = [migrations.RunSQL(
        sql="""
        DROP TRIGGER IF EXISTS catalog_segment_ai;
        DROP TRIGGER IF EXISTS catalog_segment_ad;
        DROP TRIGGER IF EXISTS catalog_segment_au;
        CREATE TRIGGER catalog_segment_ai AFTER INSERT ON media_engine_catalogsegment BEGIN
          INSERT INTO catalog_segment_fts(rowid, text) VALUES (new.id, new.text);
        END;
        CREATE TRIGGER catalog_segment_ad AFTER DELETE ON media_engine_catalogsegment BEGIN
          INSERT INTO catalog_segment_fts(catalog_segment_fts, rowid, text) VALUES('delete', old.id, old.text);
        END;
        CREATE TRIGGER catalog_segment_au AFTER UPDATE OF text ON media_engine_catalogsegment BEGIN
          INSERT INTO catalog_segment_fts(catalog_segment_fts, rowid, text) VALUES('delete', old.id, old.text);
          INSERT INTO catalog_segment_fts(rowid, text) VALUES (new.id, new.text);
        END;
        INSERT INTO catalog_segment_fts(catalog_segment_fts) VALUES('rebuild');
        """,
        reverse_sql=migrations.RunSQL.noop,
    )]
