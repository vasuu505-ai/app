# test_suite.py
"""Comprehensive test suite for the FreeNumber backend API (Numbers Management & Push Notifications)"""
import unittest
import json
import time
from main import app, ADMIN_PASSWORD
from shared_storage import shared_storage

class FreeNumberApiTests(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_root_and_health(self):
        res = self.app.get('/')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get('service'), 'FreeNumber API')

        res_h = self.app.get('/health')
        self.assertEqual(res_h.status_code, 200)
        data_h = res_h.get_json()
        self.assertEqual(data_h.get('status'), 'ok')
        self.assertIn('numbers', data_h)
        self.assertIn('push_subscribers', data_h)

    def test_admin_ui_route(self):
        res = self.app.get('/admin')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'FreeNumber Admin', res.data)
        self.assertIn(b'Admin Security Access', res.data)

    def test_admin_security_verify(self):
        # Invalid password
        res = self.app.post('/api/admin/verify', json={'password': 'wrong_passcode'})
        self.assertEqual(res.status_code, 401)

        # Valid password in body
        res = self.app.post('/api/admin/verify', json={'password': ADMIN_PASSWORD})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json().get('success'))

        # Valid password in X-Admin-Key header
        res = self.app.post('/api/admin/verify', headers={'X-Admin-Key': ADMIN_PASSWORD})
        self.assertEqual(res.status_code, 200)

    def test_numbers_endpoint_schema(self):
        res = self.app.get('/api/numbers')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        numbers = data.get('numbers', [])
        self.assertGreater(len(numbers), 0)

        first = numbers[0]
        required_keys = ['id', 'number', 'countryCode', 'country', 'flag', 'status', 'supported_apps', 'blocked_apps']
        for k in required_keys:
            self.assertIn(k, first, f"Missing key in /api/numbers: {k}")

        self.assertIsInstance(first['supported_apps'], list)
        self.assertIsInstance(first['blocked_apps'], list)

    def test_apps_endpoint(self):
        res = self.app.get('/api/apps')
        self.assertEqual(res.status_code, 200)
        apps = res.get_json()
        self.assertIsInstance(apps, list)
        self.assertGreater(len(apps), 0)

        wa = next((a for a in apps if a['id'] == 'whatsapp'), None)
        self.assertIsNotNone(wa)
        self.assertIn('name', wa)
        self.assertIn('icon_slug', wa)
        self.assertIn('available_countries_count', wa)
        self.assertIn('status', wa)

    def test_compatibility_stubs(self):
        # /api/numbers/{number}/messages should return safe empty list without error
        res_msg = self.app.get('/api/numbers/79775594420/messages')
        self.assertEqual(res_msg.status_code, 200)
        msg_data = res_msg.get_json()
        self.assertEqual(msg_data.get('total_messages'), 0)
        self.assertEqual(msg_data.get('messages'), [])

        # /api/otps stub
        res_otps = self.app.get('/api/otps')
        self.assertEqual(res_otps.status_code, 200)
        self.assertEqual(res_otps.get_json().get('total'), 0)

    def test_admin_add_and_edit_number(self):
        headers = {'X-Admin-Key': ADMIN_PASSWORD}
        # Add new number
        payload = {
            'country': 'Germany',
            'countryCode': '+49',
            'flag': '🇩🇪',
            'numbers': ['4915123456789'],
            'status': 'active',
            'supported_apps': ['whatsapp', 'uber', 'telegram'],
            'blocked_apps': ['paypal']
        }
        res = self.app.post('/api/admin/numbers', headers=headers, json=payload)
        self.assertEqual(res.status_code, 200)
        
        # Verify it appears in /api/numbers
        res_list = self.app.get('/api/numbers?country=%2B49')
        data = res_list.get_json()
        self.assertGreaterEqual(data.get('total'), 1)
        added_num = next((n for n in data['numbers'] if n['number'] == '4915123456789'), None)
        self.assertIsNotNone(added_num)
        self.assertIn('uber', added_num['supported_apps'])

        # Update number status to inactive
        res_put = self.app.put(f'/api/admin/numbers/{added_num["id"]}', headers=headers, json={
            'status': 'inactive',
            'supported_apps': ['telegram']
        })
        self.assertEqual(res_put.status_code, 200)

        # Delete number
        res_del = self.app.delete(f'/api/admin/numbers/{added_num["id"]}', headers=headers)
        self.assertEqual(res_del.status_code, 200)

    def test_notifications_subscribe_and_send(self):
        # Subscribe
        sub_payload = {
            'fcm_token': 'sample_device_fcm_token_123',
            'number': '79775594420'
        }
        res_sub = self.app.post('/api/notifications/subscribe', json=sub_payload)
        self.assertEqual(res_sub.status_code, 200)
        self.assertTrue(res_sub.get_json().get('success'))

        # Admin broadcast
        headers = {'X-Admin-Key': ADMIN_PASSWORD}
        notify_payload = {
            'title': 'Test Push Notification',
            'body': 'New numbers added!',
            'number': '79775594420',
            'app_id': 'whatsapp'
        }
        res_send = self.app.post('/api/admin/notifications/send', headers=headers, json=notify_payload)
        self.assertEqual(res_send.status_code, 200)
        self.assertTrue(res_send.get_json().get('success'))

if __name__ == '__main__':
    unittest.main()
