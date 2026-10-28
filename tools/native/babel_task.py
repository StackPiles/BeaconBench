"""One selected task using the pinned native runner and grader, no target retry."""
import argparse,contextlib,importlib.util,json,os,pathlib,signal,sys,time

def load(path,name):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def main():
 p=argparse.ArgumentParser();p.add_argument('--family',required=True);p.add_argument('--domain',required=True);p.add_argument('--native-id',required=True);p.add_argument('--work-root',type=pathlib.Path,required=True);p.add_argument('--source-root',type=pathlib.Path,required=True);p.add_argument('--data-root',type=pathlib.Path,required=True);p.add_argument('--seed',required=True)
 a=p.parse_args();work=a.work_root;source=a.source_root;data=a.data_root;r=pathlib.Path(__file__).resolve().parent
 def stop(*args):raise KeyboardInterrupt('Native task interrupted')
 signal.signal(signal.SIGTERM,stop)
 if a.family in ('tau3','vitabench'):
  package='tau2' if a.family=='tau3' else 'vita';base=source/'benchmarks'/a.family;sys.path.insert(0,str(base/'src'));os.chdir(base)
  env='TAU2' if a.family=='tau3' else 'VITA';os.environ[env+'_DATA_DIR']=str(data/a.family);os.environ[env+'_MODEL_CONFIG_PATH']=str(work/'models.json')
  os.environ.update(TAU2_LANGUAGE='Japanese',TAU2_BM25_TOKENIZER_CACHE=str(r/'tokenizers/tau'),TAU2_BM25_TOKENIZER_OFFLINE='1')
  domain='delivery,instore,ota' if a.domain=='cross' else a.domain
  sys.argv=[package,'run','--domain',domain,'--agent-llm','target','--user-llm','customer','--evaluator-llm','judge','--task-ids',a.native_id,'--num-trials','1','--max-concurrency','1','--seed',a.seed,'--save-to',str(work/'simulation')]
  if a.family=='tau3':sys.argv+=['--auto-resume','--max-retries','0']
  else:sys.argv+=['--language','japanese']
  module=__import__(package+'.cli',fromlist=['main']);module.main()
  result_path=(work/'simulation') if a.family=='vitabench' else work/'simulation/results.json'
  if not result_path.exists():
   paths=list(work.glob('simulation*.json'))
   if len(paths)!=1:raise ValueError('Single native simulation result not found')
   result_path=paths[0]
  results=json.loads(result_path.read_text());sims=results['simulations'];matches=[s for s in sims if str(s['task_id'])==a.native_id]
  if len(matches)!=1:raise ValueError('Native result missing/duplicate')
  record=matches[0];record['task_wall_seconds']=record['duration']
 elif a.family=='claw':
  base=source/'benchmarks/claw';sys.path.insert(0,str(base/'src'));os.chdir(base);os.environ['CLAW_EVAL_LANGUAGE']='ja'
  from claw_eval.runner.sandbox_runner import SandboxRunner
  original=SandboxRunner.start_container
  def start(self,*,run_id):return original(self,run_id=work.name+'-'+run_id)
  SandboxRunner.start_container=start
  sys.argv=['claw-eval','run','--task',str(r/'overlays/claw/tasks'/a.native_id),'--language','ja','--config',str(work/'claw.json'),'--trials','1','--trace-dir',str(work/'traces'),'--sandbox']
  from claw_eval.cli import main as cli_main
  try:cli_main()
  finally:
   # Source finally blocks remove the normal container. This extra cleanup
   # is restricted to the unique current trial's run_id after interruption.
   import docker
   client=docker.from_env()
   for c in client.containers.list(all=True,filters={'label':['app=claw-eval']}):
    if c.labels.get('run_id','').startswith(work.name+'-'):c.remove(force=True)
   client.close()
  files=list((work/'traces').rglob('*.jsonl'))
  if len(files)!=1:raise ValueError('Expected one Claw trace')
  events=[json.loads(l) for l in files[0].read_text().splitlines() if l.strip()];grades=[v for v in events if v.get('type')=='grading_result'];ends=[v for v in events if v.get('type')=='trace_end']
  if len(grades)!=1 or len(ends)!=1:raise ValueError('Missing or duplicate native Claw boundary/grading event')
  record=grades[0];record['task_wall_seconds']=ends[0]['wall_time_s']
 elif a.family in ('shopping','travel'):
  base=source/'benchmarks/deepplanning'/('shoppingplanning' if a.family=='shopping' else 'travelplanning');sys.path.insert(0,str(base));os.chdir(base)
  os.environ.update(DEEPPLANNING_DATA_DIR=str(data/'deepplanning'),DEEPPLANNING_MODELS_CONFIG=str(work/'models.json'),SHOPPING_BM25_TOKENIZER_CACHE_DIR=str(r/'tokenizers/shopping'))
  module=load(base/'run.py','beacon_native_deep_runner')
  original=module.run_step_inference
  def measured(args):
   start=time.monotonic();result=original(args);elapsed=time.monotonic()-start
   (work/'task-time.json').write_text(json.dumps({'task_wall_seconds':elapsed,'boundary':'native one-task inference stage; excludes conversion and evaluation'}))
   return result
  module.run_step_inference=measured
  if a.family=='shopping':
   import shutil
   db=work/'shopping';shutil.copytree(data/'deepplanning/shoppingplanning/database_level2_ja'/('case_'+a.native_id),db/('case_'+a.native_id))
   sys.argv=['run.py','--model','target','--level','2','--language','japanese','--workers','1','--rerun-ids',a.native_id,'--database-dir',str(db)]
   module.main()
   from evaluation.evaluation_pipeline import evaluate_single_case
   record=evaluate_single_case(db/('case_'+a.native_id))
  else:
   sys.argv=['run.py','--model','target','--conversion-model','converter','--language','japanese','--workers','1','--rerun-ids',a.native_id,'--output-dir',str(work/'travel'),'--save-intermediate']
   module.main()
   summaries=list((work/'travel').rglob('evaluation_summary.json'))
   if len(summaries)!=1:raise ValueError('Expected exactly one travel native summary')
   results=json.loads(summaries[0].read_text());records=[v for v in results['results'] if str(v['sample_id'])==a.native_id]
   if len(records)!=1:raise ValueError('Missing/duplicate travel case')
   record=records[0]
  record['task_wall_seconds']=json.loads((work/'task-time.json').read_text())['task_wall_seconds']
 else:raise ValueError('Unknown native family')
 (work/'grade.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
 sys.stdout.flush();sys.stderr.flush()
 if a.family in ('shopping','travel'):os._exit(0) # Native runner's documented tokenizer teardown workaround, after evidence is flushed.

if __name__=='__main__':main()
