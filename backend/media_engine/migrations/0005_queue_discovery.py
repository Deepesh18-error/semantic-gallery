from django.db import migrations


def move_old_jobs(apps, schema_editor):
    Job = apps.get_model("media_engine", "CatalogJob")
    Job.objects.filter(stage="extract", state="queued", attempts=0).update(stage="discover")


class Migration(migrations.Migration):
    dependencies = [("media_engine", "0004_catalogjob_cancel_requested_and_more")]
    operations = [migrations.RunPython(move_old_jobs, migrations.RunPython.noop)]
