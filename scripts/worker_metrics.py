"""Read Worker CPU percentiles (milliseconds) and errors for a bounded time window. Never prints credentials.

CPU comes from Workers Analytics, not wall-clock request timings. Cache hits that bypass the Worker
do not appear as Worker invocations; compare traffic and cache analytics too. Small samples are not a benchmark.
"""
import argparse
import datetime as dt
import json
import re
from cf_quota import call_cloudflare, load_env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minutes', type=int, default=60)
    parser.add_argument('--script', default='www')
    args = parser.parse_args()
    if not 1 <= args.minutes <= 1440 or not re.fullmatch(r'[a-zA-Z0-9_-]{1,63}', args.script):
        parser.error('Use 1–1440 minutes and a valid Worker name.')
    env = load_env()
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(minutes=args.minutes)
    query = '''query($account: String!, $script: String!, $start: Time!, $end: Time!) {
      viewer { accounts(filter:{accountTag:$account}) {
        workersInvocationsAdaptive(limit:1,filter:{scriptName:$script,datetime_geq:$start,datetime_leq:$end}) {
          sum { requests errors } quantiles { cpuTimeP50 cpuTimeP99 }
        }
      } }
    }'''
    result = call_cloudflare('https://api.cloudflare.com/client/v4/graphql', {
        'Authorization': 'Bearer ' + env['CLOUDFLARE_API_TOKEN'], 'Content-Type': 'application/json'
    }, json.dumps({'query': query, 'variables': {'account':env['CLOUDFLARE_ACCOUNT_ID'], 'script':args.script,
        'start':start.isoformat(), 'end':end.isoformat()}}).encode())
    if result.get('errors'):
        raise SystemExit('Workers Analytics rejected the query. Check account permissions and schema.')
    rows = result['data']['viewer']['accounts'][0]['workersInvocationsAdaptive']
    if not rows:
        print(json.dumps({'script':args.script, 'minutes':args.minutes, 'requests':0}))
        return
    row = rows[0]
    print(json.dumps({'script':args.script, 'start_utc':start.isoformat(), 'end_utc':end.isoformat(),
        'requests':row['sum']['requests'], 'errors':row['sum']['errors'],
        'cpu_p50_ms':row['quantiles']['cpuTimeP50']/1000, 'cpu_p99_ms':row['quantiles']['cpuTimeP99']/1000}, indent=2))


if __name__ == '__main__':
    main()
