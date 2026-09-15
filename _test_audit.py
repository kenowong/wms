import urllib.request, json, urllib.error
BASE='http://127.0.0.1:8902/api'
def req(method, path, body=None, token=None):
    url=BASE+path
    data=json.dumps(body).encode() if body is not None else None
    r=urllib.request.Request(url, data=data, method=method)
    r.add_header('Content-Type','application/json')
    if token: r.add_header('X-Token', token)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw=e.read().decode()
        try: return e.code, json.loads(raw)
        except: return e.code, raw

st,body=req('POST','/auth/login',{'username':'admin','password':'admin123'})
print('LOGIN',st,body)
token=body['token']

st,body=req('POST','/bank_accounts',{'name':'测试账户','opening_balance':0},token)
print('BANK',st,body)
bank_id=body['id']

# 1) 不封账：创建并审核
st,body=req('POST','/payments',{'pay_type':1,'amount':100,'bank_account_id':bank_id,'pay_date':'2026-08-09'},token)
print('CREATE_NO_CLOSE',st,body)
pid=body['id']
st,body=req('POST',f'/payments/{pid}/approve',token=token)
print('>>> APPROVE_NO_CLOSE',st,body)

# 2) 封账 2026 年
st,body=req('POST','/period/close',{'year':2026},token)
print('PERIOD_CLOSE',st,body)

# 3) 封账后：创建并审核 —— 期望业务提示(400)而非 500
st,body=req('POST','/payments',{'pay_type':1,'amount':50,'bank_account_id':bank_id,'pay_date':'2026-08-09'},token)
print('CREATE_CLOSED',st,body)
pid2=body['id']
st,body=req('POST',f'/payments/{pid2}/approve',token=token)
print('>>> APPROVE_CLOSED',st,body)
