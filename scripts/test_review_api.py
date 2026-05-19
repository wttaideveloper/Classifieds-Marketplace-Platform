import json
import urllib.request
import urllib.error

base = 'http://127.0.0.1:8000'

payload_register = {
    'username': 'testuser',
    'email': 'testuser@gmail.com',
    'password': '123456'
}

payload_login = {
    'email': 'testuser@gmail.com',
    'password': '123456'
}

payload_review = {
    'business_id': 'b1402a2b-f0d6-4f6a-b1a0-d5c9c84a6fff',
    'booking_id': '2c9f8f13-7d1a-4bd2-a5a0-5b9e9cbce762',
    'rating': 5,
    'review_comment': 'Great service!',
    'listing_id': 'dd4daa4a-a473-4f72-a08a-1355ff4be798'
}

headers = {'Content-Type': 'application/json'}


def post(path, data, extra_headers=None):
    h = headers.copy()
    if extra_headers:
        h.update(extra_headers)
    req = urllib.request.Request(base + path, data=json.dumps(data).encode('utf-8'), headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8')
    except Exception as e:
        return 'ERR', str(e)

print('REGISTER', post('/api/v1/users/register', payload_register))
status, body = post('/api/v1/users/login', payload_login)
print('LOGIN', status, body)
if status == 200:
    token = json.loads(body)['access_token']
    status2, body2 = post('/api/v1/reviews', payload_review, {'Authorization': f'Bearer {token}'})
    print('REVIEW', status2, body2)
else:
    print('SKIPPED REVIEW DUE TO LOGIN FAILURE')
