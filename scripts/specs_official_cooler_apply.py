"""Envoltorio: aplica el dry-run de specs oficiales de cooler (ver specs_official_casecooler_apply.py).
  docker exec -i stockfinder-v2-neobyte-pipeline-1 python - /output/<dryrun>.json <inicio> <fin> [--apply] < scripts/specs_official_cooler_apply.py"""
import sys, runpy
sys.argv = [sys.argv[0], "cooler"] + sys.argv[1:]
runpy.run_path("/app/scripts/specs_official_casecooler_apply.py", run_name="__main__")
