"""Generate the proposed contract only. No routes or application settings are modified."""
from copy import deepcopy
import json,re
from pathlib import Path
from fastapi import FastAPI
from app.api.v1.endpoints import service_booking as b, service, payment, refund
app=FastAPI(title="Unified Service Booking — contract for review",version="proposal-1")
for router,prefix in [(service.router,"services"),(b.service_router,"services"),(b.router,"service-bookings"),(b.enterprise_router,"enterprises"),(b.me_router,"users/me/service-bookings"),(payment.router,"payments"),(refund.router,"refunds")]:
    app.include_router(router,prefix="/api/v1/"+prefix)
doc=app.openapi();paths=doc["paths"];schemas=doc["components"]["schemas"]
def ref(name):return {"$ref":"#/components/schemas/"+name}
def obj(**props):return {"type":"object","properties":props,"required":list(props)}
def param(name,loc,schema):return {"name":name,"in":loc,"required":True,"schema":schema}
def nullable(s):return {"anyOf":[s,{"type":"null"}]}
uid={"type":"string","format":"uuid"};dt={"type":"string","format":"date-time"};string={"type":"string"}
for p in list(paths):
    if p.startswith('/api/v1/services/') and not (p.endswith(('available-slots','booking-settings','/services/','/services/{service_id}'))):del paths[p]
    elif p.startswith(('/api/v1/payments','/api/v1/refunds')) and p not in ('/api/v1/payments','/api/v1/refunds','/api/v1/refunds/{refund_id}/decision','/api/v1/payments/{payment_id}/verify'):del paths[p]
for path,ops in paths.items():
    for op in ops.values():op['x-stage']='existing route; target contract subject to review'
doc['info']['description']='Review only; not a deployed contract. See service-booking-review.md for state transitions, compatibility, financial and concurrency guarantees.'
for name in ['CancelBody','DecisionBody','StatusBody']:
    schemas[name]['properties'].pop('refund_percent',None);schemas[name]['additionalProperties']=False
schemas['BookingCreate']['properties'].pop('paid',None);schemas['BookingCreate']['additionalProperties']=False
detail=schemas['BookingDetail']['properties']
for prop in ('payment_id','invoice_id','refund_id'):detail[prop]=nullable(uid)
detail['refund_status']=nullable({'type':'string','enum':['requested','approved','processing','completed','rejected','failed']})
for model in ('BookingListItem','BookingDetail'):
    props=schemas[model]['properties'];props['can_reschedule']['description']='True only when the reschedule endpoint is enabled and the actor and slot are eligible.'
    props['payment_status']['enum']+=['processing','failed','expired']
    props['allowed_actions']={'type':'array','items':{'type':'string','enum':['confirm','cancel','request_cancellation','approve_cancellation','reject_cancellation','start','complete','no_show','join','reschedule','pay']}}
schemas['PaymentRequest']['properties']['purchase_type']['enum'].append('service_booking')
schemas['PaymentRequest']['properties']['purchase_id']['description']='Required booking UUID for service_booking; server prices the reserved booking.'
for path,ops in paths.items():
    for method,op in ops.items():
        if path.endswith(('/pay','/payment')) and 'service-bookings/' in path:
            op['deprecated']=True;op['description']='Legacy status-only endpoint; never use as proof of funds or for gateway-backed bookings.'
        if path.endswith('service-bookings') and method=='post':
            op['description']='Self booking or scoped admin manual booking. Paid booking creates a hold; initiate checkout with POST /payments. Replaying same key and body returns 200.'
            for arg in op.get('parameters',[]):
                if arg['name']=='Idempotency-Key':arg.update(required=True,schema={'type':'string','minLength':1,'maxLength':128})
            op['responses']['200']=deepcopy(op['responses']['201'])
for path in ['/api/v1/service-bookings/{booking_id}/cancellation/approve','/api/v1/service-bookings/{booking_id}/cancellation/reject']:
    paths[path]['post']['description']='Tenant-scoped admin decision. Refund amount derives from the policy snapshot. No amount override.'
def add(path,method,summary,params,resp,body=None,media='application/json',stage='proposed'):
    op={'operationId':summary.lower().replace(' ','_'),'summary':summary,'x-stage':stage,'security':[{'BearerAuth':[]}],'parameters':params,'responses':{'200':{'description':'Success','content':{'application/json':{'schema':resp}}}}}
    if body:op['requestBody']={'required':True,'content':{media:{'schema':body}}}
    paths[path]={method:op}
base='/api/v1/service-bookings/{booking_id}'
bid=param('booking_id','path',uid)
add(base+'/reschedule','post','Reschedule booking atomically',[bid,param('Idempotency-Key','header',string)],ref('BookingDetail'),obj(starts_at=dt))
add(base+'/join','get','Read authorized join information',[bid],obj(provider=nullable(string),url={'type':'string','format':'uri'},passcode=nullable(string),instructions=nullable(string),joins_at=dt))
add('/api/v1/enterprises/{enterprise_id}/providers/{provider_id}/availability','get','Read scoped provider availability',[param('enterprise_id','path',uid),param('provider_id','path',uid),param('service_id','query',uid),param('date','query',{'type':'string','format':'date'})],ref('SlotsResponse'))
add('/api/v1/services/banner-uploads','post','Upload public service banner',[param('enterprise_id','query',uid)],obj(url=string,size={'type':'integer'},type=string),obj(file={'type':'string','format':'binary'}),'multipart/form-data')
schemas['BookingError']=obj(detail=obj(code=string,message=string))
def error(code,message):return {'detail':{'code':code,'message':message}}
book=paths['/api/v1/service-bookings']['post']
for sc,examples in [('409',[('SLOT_FULL','No seats remain for this party'),('PROVIDER_UNAVAILABLE','Provider has an overlapping appointment'),('IDEMPOTENCY_CONFLICT','Key already used for another request')]),('403',[('BOOK_FOR_SELF_ONLY','Customers may only book for themselves')])]:
    book['responses'][sc]={'description':'Booking refused','content':{'application/json':{'schema':ref('BookingError'),'examples':{k:{'value':error(k,v)} for k,v in examples}}}}
paths[base]['get']['responses']['404']={'description':'Missing or inaccessible','content':{'application/json':{'schema':ref('BookingError'),'example':error('BOOKING_NOT_FOUND','Booking not found')}}}
for path in [base+'/join',base+'/reschedule']:
    op=next(iter(paths[path].values()));op['responses']['409']={'description':'Not yet joinable, target slot unavailable or booking closed','content':{'application/json':{'schema':ref('BookingError')}}}
sid='550e8400-e29b-41d4-a716-446655440001'; booking=sid[:-1]+'2';start='2026-11-20T09:00:00+05:30'
reqs={n:{'value':{'service_id':sid,'starts_at':start,'delivery_mode':m,'participants':1}} for n,m in [('freeInPerson','in_person'),('paidVirtual','virtual'),('freeHybrid','hybrid')]}
book['requestBody']['content']['application/json']['examples']=reqs
common=dict(id=booking,status='confirmed',payment_status='not_required',service_id=sid,service_name='Consultation',provider_id=sid[:-1]+'3',provider_name='Dr Rao',enterprise_id=sid[:-1]+'4',enterprise_name='Clinic',delivery_mode='in_person',starts_at=start,ends_at='2026-11-20T09:30:00+05:30',timezone='Asia/Kolkata',participants=1,amount=0,currency='INR',can_cancel=True,cancel_mode='instant',can_reschedule=False,can_join=False,can_check_in=False,customer={'user_id':sid[:-1]+'5','name':'Anu'},duration_minutes=30,venue={'name':'Clinic','line1':'12 Main Road','city':'Pune','instructions':'Reception'},meeting=None,cancellation_policy_summary='Free until 24 hours before appointment',cancellation={},payment_id=None,invoice_id=None,refund_id=None,refund_status=None,history=[],allowed_actions=['cancel'])
examples={}
for n,r in reqs.items():
    x=deepcopy(common);x['delivery_mode']=r['value']['delivery_mode']
    if n!='freeInPerson':x['meeting']={'provider':'zoom','available':False,'joins_at':'2026-11-20T08:50:00+05:30','url':None,'passcode':None,'instructions':None}
    if n=='paidVirtual':x.update(status='pending',payment_status='unpaid',amount=500,venue=None,hold_expires_at='2026-11-19T10:15:00+05:30',allowed_actions=['pay','cancel'])
    examples[n]={'value':x}
book['responses']['201']['content']['application/json']['examples']=examples
# Remove status-only language and add examples to the target operations.
for key in ("BookingDetail","BookingListItem"):
    props=schemas[key]["properties"]
    props["can_check_in"]["description"]="False until customer check-in is implemented."
for key in ("BookingCreate","CancelBody","StatusBody","DecisionBody"):
    schemas[key]["additionalProperties"]=False
paths[base+"/status"]["patch"]["description"]="Scoped admin transitions; assigned providers may only start, complete or mark no-show. No refund override."
paths[base+"/cancel"]["post"]["description"]="Evaluate policy snapshot. Legacy free-text terms go to manual review; no invented automatic refund."
def body_example(path,value):
    media=paths[path]["post"]["requestBody"]["content"]["application/json"]
    media.pop("examples",None);media["example"]=value
body_example(base+"/cancel",{"reason":"Unable to attend"})
body_example(base+"/cancellation/reject",{"reason":"Appointment has already started"})
body_example(base+"/cancellation/approve",{"reason":"Approved under the saved policy"})
body_example("/api/v1/payments",{"purchase_type":"service_booking","purchase_id":booking})
for suffix,state,cancelled in [("/cancel","cancellation_requested",False),("/cancellation/approve","cancelled",True),("/cancellation/reject","confirmed",False)]:
    snap=deepcopy(common);snap.update(status=state,can_cancel=state=="confirmed",allowed_actions=["cancel"] if state=="confirmed" else [],
        cancel_mode="instant" if state=="confirmed" else None,cancellation={"reason":"Unable to attend","decision_reason":"Policy decision" if suffix!="/cancel" else None})
    paths[base+suffix]["post"]["responses"]["200"]["content"]["application/json"]["example"]=snap
failed=deepcopy(examples["paidVirtual"]["value"]);failed.update(status="expired",payment_status="failed",hold_expires_at=None,can_cancel=False,cancel_mode=None,allowed_actions=[])
paths[base]["get"]["responses"]["200"]["content"]["application/json"]["examples"]={"failedPayment":{"value":failed}}
# Keep only schemas reachable from the routes, including transitive references.
import collections
queue=collections.deque(re.findall(r'#/components/schemas/([^"]+)',json.dumps(paths)));keep=set()
while queue:
    n=queue.popleft()
    if n not in keep:
        keep.add(n);queue.extend(re.findall(r'#/components/schemas/([^"]+)',json.dumps(schemas[n])))
doc['components']['schemas']={n:schemas[n] for n in sorted(keep)}
for path,ops in paths.items():
    for op in ops.values():
        assert set(re.findall(r"\{([^}]+)\}",path))=={param["name"] for param in op.get("parameters",[]) if param.get("in")=="path"},path
        for requirement in op.get("security",[]):
            assert set(requirement)<=set(doc["components"]["securitySchemes"])
for example in examples.values():
    assert set(schemas["BookingDetail"]["required"])<=example["value"].keys()
out=Path(__file__).with_name('service-booking-review.openapi.json')
out.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(len(paths),'paths;',len(keep),'reachable schemas')
