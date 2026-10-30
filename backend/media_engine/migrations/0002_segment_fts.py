from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("media_engine", "0001_initial")]
    operations = [migrations.RunSQL(
        sql="""
        CREATE VIRTUAL TABLE catalog_segment_fts USING fts5(text, content='media_engine_catalogsegment', content_rowid='id');
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
        reverse_sql="""
        DROP TRIGGER catalog_segment_au;
        DROP TRIGGER catalog_segment_ad;
        DROP TRIGGER catalog_segment_ai;
        DROP TABLE catalog_segment_fts;
        """,
    )]
