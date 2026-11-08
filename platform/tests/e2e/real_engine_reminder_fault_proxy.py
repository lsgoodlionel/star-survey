from http.server import BaseHTTPRequestHandler,HTTPServer
import urllib.request,urllib.error,json
first=True
attempts=0
forwarded=0
successes=0
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):
  if self.path!='/_fixture/metrics':self.send_error(404);return
  content=json.dumps({'attempts':attempts,'forwarded':forwarded,'successes':successes}).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
 def do_POST(self):
  global first,attempts,forwarded,successes
  attempts+=1
  body=self.rfile.read(int(self.headers.get('Content-Length','0')))
  if self.path!='/v1/responses/read':self.send_error(404);return
  if first:
   first=False;self.send_response(503);self.end_headers();return
  headers={k:v for k,v in self.headers.items() if k.lower() not in ('host','connection','content-length')}
  forwarded+=1
  request=urllib.request.Request('http://real-gateway:8080'+self.path,data=body,headers=headers,method='POST')
  try:
   with urllib.request.urlopen(request,timeout=30) as response: status=response.status; content=response.read()
  except urllib.error.HTTPError as e:status=e.code;content=e.read()
  if status==200:successes+=1
  self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
HTTPServer(('0.0.0.0',8080),Handler).serve_forever()
