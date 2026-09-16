import assert from 'node:assert/strict';
import {capture} from './capture.mjs';
function api(changed=false){
  let calls=0;const local={},session={};
  const storage=o=>({get:async()=>({...o}),set:async v=>Object.assign(o,v)});
  return {storage:{local:storage(local),session:storage(session)},tabs:{query:async()=>{
    calls++;return [{id:1,windowId:1,index:0,groupId:4,url:'https://youtu.be/abcdefghijk',title:'Talk'},
      {id:2,windowId:2,index:0,groupId:-1,url:'https://private.test',incognito:true},
      ...(changed&&calls>1?[{id:3,url:'https://example.test',groupId:-1}]:[])];
  }},tabGroups:{query:async()=>[{id:4,title:'AGI'}]}};
}
const a=api();const first=await capture(a,'test');const second=await capture(a,'test');
assert.equal(first.status,'complete');assert.equal(first.tabs.length,1);
assert.equal(first.scope.id,second.scope.id);assert.equal(first.session_id,second.session_id);
assert.equal((await capture(api(true),'test')).status,'partial');
const broken=api();broken.tabGroups.query=async()=>[];
assert.equal((await capture(broken,'test')).status,'partial');
console.log('Capture tests passed: scope persistence, incognito exclusion, changing tabs, missing groups.');
