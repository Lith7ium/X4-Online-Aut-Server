local M=dofile('src/authority/lua/replica_motion_binding.lua')
local normalize=M.normalize_profile_save_path
assert(normalize('/home/test/45841/save')=='/home/test/45841/save')
assert(normalize('C:/Users/Test/Documents/Egosoft/X4/45841/save')=='C:/Users/Test/Documents/Egosoft/X4/45841/save')
assert(normalize('C:\\Users\\Test\\Documents\\Egosoft\\X4\\45841\\save\\')=='C:/Users/Test/Documents/Egosoft/X4/45841/save')
assert(normalize('C:/Users//Test/45841/save')=='C:/Users//Test/45841/save')
assert(normalize('\\\\Server\\Share\\Users\\Test\\45841\\save')=='//Server/Share/Users/Test/45841/save')
for _,path in ipairs({'relative/45841/save','/home/test/45842/save','C:/Users/Test/../45841/save',
 '\\\\server\\share\\Users\\..\\45841\\save','//server/share','//server/share/Users/Test/45841/save/\0',
 'C:/Users/Test//Documents/../45841/save','//?/C:/Users/Test/45841/save',
 '\\Users\\Test\\45841\\save','\\??\\C:\\Users\\Test\\45841\\save'})do
 assert(normalize(path)==nil,path)
end
assert(normalize('C:\\Users\\Test\\45850\\save\\','45850')=='C:/Users/Test/45850/save')
assert(normalize('//Server/Share/Users/Test/45858/save','45858')=='//Server/Share/Users/Test/45858/save')
assert(normalize('/home/test/45850/save','45850')==nil)
assert(normalize('C:/Users/Test/45850/save')==nil)
assert(normalize('C:/Users/Test/45850/save','45851')==nil)
assert(normalize('C:/Users/Test/45850/save',45850)==nil)
assert(normalize('C:/Users/Test/45859/save','45859')==nil)
assert(normalize('//Server/45850/save','45850')==nil)
local epoch,generation,nonce=string.rep('a',32),string.rep('b',32),string.rep('c',32)
local function sample(seq)return {epoch=epoch,generation=generation,index=42,sequence=seq or 1,sector_macro='sector_a'}end
local function rig(mode,custom_nonce)
 local t=10;local issued,completed={},{};local client;local count=0
 client=assert(M.new({enabled=true,epoch=epoch,generation=generation,index=42,sector_macro='sector_a',nonce=custom_nonce or nonce,timeout=3,max_age=2,
  clock=function()return t end,guard=function()return mode~='guard' end,
  issue=function(screen,op,args)assert(screen=='X4AuthorityReplicaMotionBind' and op=='Bind');issued[#issued+1]=args;return true end,
  inspect=function(a,s,index,sector)
   assert(a=='0x20000000000001' and s=='0x2b' and index==42 and sector=='sector_a');count=count+1
   if mode=='ffi' then return nil,nil,false end
   return '9007199254740993','43',true,mode=='zone' and count>1 and '46' or '45'
  end,
  complete=function(r)completed[#completed+1]=r;if mode=='reentrant' then client:close() end;if mode=='mutate' then r.index=43 end;return true end}))
 return client,issued,completed,function(n)t=n end
end
local function response(q)return q[#q].request_id..'|Bound|42|0x20000000000001|0x2b' end
do
 local c,q,done=rig();assert(c:begin(sample(),10));assert(c:event(response(q)));assert(#done==1 and done[1].index==42 and done[1].sequence==1)
 for k in pairs(done[1])do assert(k=='epoch' or k=='generation' or k=='index' or k=='sequence' or k=='sector_macro')end
 assert(c:begin(sample(2),10));assert(c:event(response(q)) and #done==2 and c:state()=='idle')
 assert(not c:begin(sample(2),10) and c:state()=='failed')
end
for _,field in ipairs({'epoch','generation','index','sector_macro','sequence'})do
 local c,q,done=rig();local v=sample();v[field]=field=='sequence' and 0 or 'wrong'
 assert(not c:begin(v,10) and #q==0 and #done==0 and c:state()=='failed')
end
for _,arrival in ipairs({7,11})do local c,q=rig();assert(not c:begin(sample(),arrival) and #q==0)end
do local c,q,done,set=rig();assert(c:begin(sample(),10));set(12.1);assert(not c:event(response(q)) and #done==0)end
do local c,q,done=rig();assert(c:begin(sample(),10));assert(not c:event(q[1].request_id..'|Unavailable|') and #done==0)end
do local c,q,done=rig();assert(c:begin(sample(),10));assert(not c:event(nonce..'-42-9|Bound|42|0x20000000000001|0x2b') and #done==0)end
for _,mode in ipairs({'ffi','reentrant','mutate'})do
 local c,q,done=rig(mode);assert(c:begin(sample(),10));assert(not c:event(response(q)) and c:state()=='failed')
end
do local c,q,done=rig('zone');assert(c:begin(sample(),10));assert(c:event(response(q)));assert(c:begin(sample(2),10));assert(not c:event(response(q)) and #done==1)end
do local c,q,done=rig();assert(c:begin(sample(),10));assert(c:event(response(q)));assert(c:begin(sample(2),10));assert(not c:event(q[2].request_id..'|Bound|42|0x20000000000002|0x2b') and #done==1)end
do
 local n1=('e'):rep(16)..('1'):rep(16);local n2=('e'):rep(16)..('2'):rep(16)
 local c1,q1=rig(nil,n1);local c2,q2,done=rig(nil,n2)
 assert(c1:begin(sample(),10) and c2:begin(sample(),10))
 assert(q1[1].request_id==n1..'-42-1' and q2[1].request_id==n2..'-42-1')
 assert(not c2:event(q1[1].request_id..'|Bound|42|0x20000000000001|0x2b') and #done==0)
end
assert(M.new({enabled=false})==nil)
print('replica binding synthetic checks passed')
