import {capture} from './capture.mjs';
const profile=document.querySelector('#profile'), status=document.querySelector('#status'), button=document.querySelector('#capture');
profile.value=(await chrome.storage.local.get('profile')).profile||'';
button.addEventListener('click',async()=>{
  if(!profile.value.trim()){status.textContent='Enter a profile name first.';return;}
  button.disabled=true;
  try {
    const snapshot=await capture(chrome,profile.value.trim());
    const stamp=snapshot.captured_at.replace(/[:.]/g,'-');
    const url='data:application/json;charset=utf-8,'+encodeURIComponent(JSON.stringify(snapshot,null,2));
    await chrome.downloads.download({url,filename:`BrowserAttention/browser-attention-${stamp}.json`,saveAs:false,conflictAction:'uniquify'});
    status.textContent=`Export started: ${snapshot.tabs.length} tabs, ${snapshot.groups.length} groups (${snapshot.status}).\nCheck Downloads for completion. Then run Browser Attention Update.`;
  } catch(e){status.textContent=`Capture failed: ${e.message}`;}
  finally{button.disabled=false;}
});
