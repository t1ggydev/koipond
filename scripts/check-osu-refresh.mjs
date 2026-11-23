import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import ts from 'typescript';
const mocks={};
function load(path){const exports={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(path,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports,require:n=>{if(n in mocks)return mocks[n];throw Error('Unexpected import '+n)},process,URL,Response,AbortSignal,setTimeout:fn=>{fn();},console});return exports;}
mocks['server-only']={};mocks['@/lib/env']={env:{osuClientId:'fake',osuClientSecret:'fake'}};
let profileStatus=200;let topStatus=200;let fetchCount=0;
mocks['@/lib/boundedFetch']={boundedFetch:async url=>{fetchCount++;if(url.endsWith('/oauth/token'))return Response.json({access_token:'fake',expires_in:3600});if(url.includes('/scores/'))return Response.json([], {status:topStatus});return Response.json({statistics:{}},{status:profileStatus});}};
const osu=load('src/lib/osu.ts');
for(const status of [401,403,429,500,503]){profileStatus=status;await assert.rejects(()=>osu.fetchOsuStats(7),e=>!(e instanceof osu.OsuProfileNotFoundError)&&e.message.includes(String(status)));}
profileStatus=404;await assert.rejects(()=>osu.fetchOsuStats(7),e=>e instanceof osu.OsuProfileNotFoundError);
profileStatus=200;topStatus=404;await osu.fetchOsuStats(7); // A scores 404 is not a missing profile.
let missing=false;let writeError=null;let guardedId;let calls=0;let logs=0;let upsert;
const row={id:'participant',osu_id:7,osu_username:'synthetic',osu_stats:null};
const db={from:table=>({select(){return this},is(column,value){assert.equal(column,'osu_profile_missing_at');assert.equal(value,null);return Promise.resolve({data:missing?[]:[row],error:null})},update(value){return {eq(column,value2){if(column==='id')guardedId=value2;return this},then(resolve){if(!writeError)missing=Boolean(value.osu_profile_missing_at);resolve({error:writeError})}}},upsert(value){if(table==="participants")upsert=value;return {select(){return this},single:async()=>({data:{id:'participant'},error:null}),then(resolve){resolve({error:null})}}}})};
mocks['@/lib/supabaseServer']={supabaseAdmin:()=>db};mocks['@/lib/osu']={...osu,fetchOsuStats:async()=>{calls++;if(profileStatus===404)throw new osu.OsuProfileNotFoundError();if(profileStatus!==200)throw Error('HTTP '+profileStatus);return {}}};mocks['@/lib/participants']={saveOsuStats:async()=>{}};mocks['node:crypto']={randomUUID:()=> 'fake'};mocks['@/lib/operationalQueue']={durableEventsEnabled:()=>false};mocks['next/server']={NextResponse:{json:data=>Response.json(data)}};
const original=console.error;console.error=()=>logs++;
try{
 const route=load('src/app/api/cron/refresh-osu-stats/route.ts');process.env.CRON_SECRET='fake';const req={headers:new Headers({authorization:'Bearer fake'}),nextUrl:new URL('http://localhost/?limit=10')};
 profileStatus=404;let r=await (await route.GET(req)).json();assert.equal(r.skippedMissing,1);assert.equal(r.failed,0);assert.equal(logs,0);assert.equal(guardedId,'participant');
 await route.GET(req);assert.equal(calls,1,'second run must not fetch missing profile');
 missing=false;profileStatus=503;r=await (await route.GET(req)).json();assert.equal(r.failed,1);assert.equal(missing,false);assert.equal(logs,1);
 profileStatus=404;writeError={message:'database unavailable'};r=await (await route.GET(req)).json();assert.equal(r.failed,1);assert.equal(r.skippedMissing,0);assert.equal(logs,2);writeError=null;
 mocks['@/lib/osu']={fetchOsuStats:async()=>({})};const participants=load('src/lib/participants.ts');await participants.linkParticipant({twitchId:'synthetic'},{id:7,username:'synthetic'});assert.equal(upsert.osu_profile_missing_at,null);
}finally{console.error=original;delete process.env.CRON_SECRET;}
assert(fetchCount>0);console.log('PASS: profile 404 skipped persistently; other errors visible; write failure visible; OAuth relink reactivates');
