// Local fake queue endpoint: never uses production data or external APIs.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { once } from "node:events";
const arrivals=[];
const server=createServer((req,res)=>{
  assert.equal(req.url,"/api/internal/worker/run");
  arrivals.push(performance.now());
  res.writeHead(200,{"Content-Type":"application/json"});
  res.end(JSON.stringify({processed:arrivals.length===3}));
});
server.listen(0,"127.0.0.1");await once(server,"listening");
const child=spawn(process.execPath,["scripts/worker.mjs"],{
  env:{...process.env,KOIPOND_WORKER_TOKEN:"isolated-worker-latency-token-not-for-production",KOIPOND_WORKER_URLS:`http://127.0.0.1:${server.address().port}/api/internal/worker/run`},stdio:"ignore",
});
const exited=once(child,"exit");
try {
  const deadline=Date.now()+5000;
  while(arrivals.length<5&&Date.now()<deadline)await new Promise(r=>setTimeout(r,10));
  assert(arrivals.length>=5,"Worker must continue polling");
  const idle=arrivals[2]-arrivals[1],busy=arrivals[3]-arrivals[2];
  assert(idle>=200&&idle<600,`Idle polling should take about 250ms, measured ${idle}ms`);
  assert(busy<200,`Queued work should drain without an extra idle delay, measured ${busy}ms`);
  console.log(JSON.stringify({result:"PASS",idlePollMs:Math.round(idle),afterWorkMs:Math.round(busy),externalCalls:0}));
} finally {child.kill("SIGTERM");await exited;server.close();}
