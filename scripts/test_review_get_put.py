import json
import uuid
import urllib.request
import urllib.error

base = 'http://127.0.0.1:8000'

headers = {'Content-Type': 'application/json'}


def post(path, data, extra_headers=None):
    h = headers.copy()
    if extra_headers:
        h.update(extra_headers)
    req = urllib.request.Request(base + path, data=json.dumps(data).encode('utf-8'), headers=h, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8')
    except Exception as e:
        return 'ERR', str(e)


def put(path, data, extra_headers=None):
    h = headers.copy()
    if extra_headers:
        h.update(extra_headers)
    req = urllib.request.Request(base + path, data=json.dumps(data).encode('utf-8'), headers=h, method='PUT')
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8')
    except Exception as e:
        return 'ERR', str(e)


def get(path, extra_headers=None):
    h = {}
    if extra_headers:
        h.update(extra_headers)
    req = urllib.request.Request(base + path, headers=h, method='GET')
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8')
    except Exception as e:
        return 'ERR', str(e)


if __name__ == '__main__':
    # Login as customer
    _, login_body = post('/api/v1/users/login', {
        'email': 'testuser@gmail.com',
        'password': '123456'
    })
    customer_token = json.loads(login_body)['access_token']
    print('customer_token', customer_token)

    # Submit a fresh review with a new booking_id to avoid duplicate errors
    booking_id = str(uuid.uuid4())
    _, review_body = post('/api/v1/reviews', {
        'business_id': 'b1402a2b-f0d6-4f6a-b1a0-d5c9c84a6fff',
        'booking_id': booking_id,
        'rating': 5,
        'review_comment': 'Great service!',
        'listing_id': 'dd4daa4a-a473-4f72-a08a-1355ff4be798'
    }, {'Authorization': f'Bearer {customer_token}'})
    print('submit review', review_body)

    review_data = json.loads(review_body)
    review_id = review_data.get('review_id')
    print('review_id', review_id)

    # Login as admin
    _, admin_body = post('/api/v1/admin/login', {
        'email': 'admin@example.com',
        'password': 'admin123'
    })
    admin_token = json.loads(admin_body)['accessToken']
    print('admin_token', admin_token)

    status, approve_body = put(f'/api/v1/admin/reviews/{review_id}', {
        'moderation_status': 'Approved',
        'remarks': 'OK'
    }, {'Authorization': f'Bearer {admin_token}'})
    print('approve review', status, approve_body)

    status, get_body = get('/api/v1/reviews/b1402a2b-f0d6-4f6a-b1a0-d5c9c84a6fff')
    print('get reviews', status, get_body)
