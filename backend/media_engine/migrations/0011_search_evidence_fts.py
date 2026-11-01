from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("media_engine", "0010_catalogembeddingcache")]
    operations = [migrations.RunSQL(
        sql="""
        CREATE VIRTUAL TABLE catalog_search_fts USING fts5(filename, title, heading, text);
        CREATE TRIGGER catalog_search_ai AFTER INSERT ON media_engine_catalogsegment BEGIN
          INSERT INTO catalog_search_fts(rowid, filename, title, heading, text)
          SELECT new.id, f.original_name, new.title, new.heading, new.text
          FROM media_engine_catalogfileversion v JOIN media_engine_catalogfile f ON f.id=v.file_id
          WHERE v.id=new.file_version_id;
        END;
        CREATE TRIGGER catalog_search_ad AFTER DELETE ON media_engine_catalogsegment BEGIN
          DELETE FROM catalog_search_fts WHERE rowid=old.id;
        END;
        CREATE TRIGGER catalog_search_au AFTER UPDATE OF text,title,heading ON media_engine_catalogsegment BEGIN
          DELETE FROM catalog_search_fts WHERE rowid=old.id;
          INSERT INTO catalog_search_fts(rowid, filename, title, heading, text)
          SELECT new.id, f.original_name, new.title, new.heading, new.text
          FROM media_engine_catalogfileversion v JOIN media_engine_catalogfile f ON f.id=v.file_id
          WHERE v.id=new.file_version_id;
        END;
        CREATE TRIGGER catalog_search_filename_au AFTER UPDATE OF original_name ON media_engine_catalogfile BEGIN
          DELETE FROM catalog_search_fts WHERE rowid IN
            (SELECT s.id FROM media_engine_catalogsegment s
             JOIN media_engine_catalogfileversion v ON v.id=s.file_version_id WHERE v.file_id=new.id);
          INSERT INTO catalog_search_fts(rowid, filename, title, heading, text)
          SELECT s.id, new.original_name, s.title, s.heading, s.text
          FROM media_engine_catalogsegment s JOIN media_engine_catalogfileversion v ON v.id=s.file_version_id
          WHERE v.file_id=new.id;
        END;
        INSERT INTO catalog_search_fts(rowid, filename, title, heading, text)
        SELECT s.id, f.original_name, s.title, s.heading, s.text
        FROM media_engine_catalogsegment s
        JOIN media_engine_catalogfileversion v ON v.id=s.file_version_id
        JOIN media_engine_catalogfile f ON f.id=v.file_id;
        """,
        reverse_sql="""
        DROP TRIGGER catalog_search_filename_au;
        DROP TRIGGER catalog_search_au;
        DROP TRIGGER catalog_search_ad;
        DROP TRIGGER catalog_search_ai;
        DROP TABLE catalog_search_fts;
        """,
    )]
