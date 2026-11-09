import json,os,subprocess,time,urllib.request,urllib.error
os.environ['MOZ_HEADLESS']='1'; evidence=json.load(open('/fixture/evidence.json'));sid=evidence['sid'];token=evidence['completedToken']
driver=subprocess.Popen(['geckodriver','--host','127.0.0.1','--port','4444'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def req(method,path,data=None):
 p=None if data is None else json.dumps(data).encode();r=urllib.request.Request('http://127.0.0.1:4444'+path,data=p,method=method,headers={'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(r,timeout=40) as x:return x.status,json.load(x)
 except urllib.error.HTTPError as e:return e.code,json.load(e)
try:
 for _ in range(80):
  try:
   if req('GET','/status')[0]==200:break
  except Exception:time.sleep(.25)
 else:raise RuntimeError('driver unavailable')
 code,r=req('POST','/session',{'capabilities':{'alwaysMatch':{'browserName':'firefox','acceptInsecureCerts':False,'moz:firefoxOptions':{'args':['-headless']}}}});assert code==200
 session=r['value']['sessionId'];code,_=req('POST',f'/session/{session}/url',{'url':f'http://127.0.0.1/index.php/{sid}?token={token}'});assert code==200
 for step in range(8):
  code,r=req('POST',f'/session/{session}/execute/sync',{'script':'''const visible=e=>e.getClientRects().length>0;const radios=[...document.querySelectorAll('input[type="radio"]')].filter(visible);if(radios.length){radios[0].click();} const buttons=[...document.querySelectorAll('#ls-button-submit,#ls-button-next,button[type="submit"],input[type="submit"]')].filter(visible);if(!buttons.length)return {advance:false,radio:radios.length};buttons[buttons.length-1].click();return {advance:true,radio:radios.length};''','args':[]});assert code==200
  if not r['value']['advance']:break
  time.sleep(1.5)
 else:raise RuntimeError('survey did not terminate')
 req('DELETE',f'/session/{session}');print('isolated Firefox respondent journey completed',flush=True)
finally:
 driver.terminate()
 try:driver.wait(timeout=5)
 except subprocess.TimeoutExpired:driver.kill();driver.wait()
