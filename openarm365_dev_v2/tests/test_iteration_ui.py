import json
from openarm365_dev_v2.tests.test_ui import api
from openarm365_dev_v2.tests.test_iteration import selection

def test_configured_goal_api_and_dynamic_cells(api):
 s,r,tmp=api
 a=json.loads(r('/api/dev/experiments',selection('api-new-config'))[1]);b=json.loads(r('/api/dev/experiments',selection('api-second-config'))[1])
 assert a['goal_id']!=b['goal_id'] and a['selection']['target_depth']==3
 assert r('/api/dev/experiments/'+a['experiment_id']+'/run',{'max_cells':2})[0]==400
 assert r('/api/dev/experiments',{**selection('api-new-config'),'max_versions':4})[0]==409
 assert r('/api/dev/experiments',{**selection('api-bad-budget'),'cell_budget':{}})[0]==400
 assert len(json.loads(r('/api/dev/resources')[1])['goals'])==2
 assert json.loads(r('/api/dev/options')[1])['configuration']['max_versions']==5
