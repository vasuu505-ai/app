# test_suite.py
"""Comprehensive test suite for the FreeNumber backend API"""
import unittest
import json
import time
import io
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
        self.assertEqual(data.get('service'), 'FreeNumber Dynamic API')

        res_h = self.app.get('/health')
        self.assertEqual(res_h.status_code, 200)
        data_h = res_h.get_json()
        self.assertEqual(data_h.get('status'), 'ok')
        self.assertIn('numbers', data_h)
        self.assertIn('otps', data_h)

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
        # Required AI Studio fields check
        required_keys = ['id', 'number', 'countryCode', 'country', 'flag', 'status', 'supported_apps', 'blocked_apps', 'received_sms_count']
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

        # Check WhatsApp in apps list
        wa = next((a for a in apps if a['id'] == 'whatsapp'), None)
        self.assertIsNotNone(wa)
        self.assertIn('name', wa)
        self.assertIn('icon_slug', wa)
        self.assertIn('available_countries_count', wa)
        self.assertIn('status', wa)
        self.assertGreaterEqual(wa['available_countries_count'], 1)
        self.assertEqual(wa['status'], 'available')

    def test_otp_enrichment_and_number_messages(self):
        test_number = "79775594420"
        sample_otp = {
            'id': f'test_msg_{int(time.time()*1000)}',
            'time': '12:30:00',
            'country': 'Russian Federation', # tests normalization to Russia
            'flag': '🇷🇺',
            'number': test_number,
            'sender': 'WhatsApp',
            'message': 'Your WhatsApp code: 484-073. Do not share.',
            'otp': '484-073'
        }

        # Add to shared_storage
        added = shared_storage.add_otp(sample_otp)
        self.assertTrue(added)

        # Check /api/otps
        res = self.app.get('/api/otps')
        self.assertEqual(res.status_code, 200)
        otps = res.get_json().get('otps', [])
        found = next((o for o in otps if o['id'] == sample_otp['id']), None)
        self.assertIsNotNone(found)
        self.assertEqual(found['clean_otp'], '484-073')
        self.assertEqual(found['app_id'], 'whatsapp')
        self.assertEqual(found['country'], 'Russia')
        self.assertEqual(found['sender'], 'WhatsApp')

        # Check Endpoint 1: GET /api/numbers/{number}/messages
        res_msg = self.app.get(f'/api/numbers/{test_number}/messages')
        self.assertEqual(res_msg.status_code, 200)
        msg_data = res_msg.get_json()
        self.assertEqual(msg_data.get('number'), test_number)
        self.assertEqual(msg_data.get('country'), 'Russia')
        self.assertGreaterEqual(msg_data.get('total_messages'), 1)
        self.assertIsInstance(msg_data.get('messages'), list)
        
        msg_item = msg_data['messages'][0]
        self.assertEqual(msg_item['sender'], 'WhatsApp')
        self.assertEqual(msg_item['app_id'], 'whatsapp')
        self.assertEqual(msg_item['clean_otp'], '484-073')
        self.assertIn('Your WhatsApp code: 484-073', msg_item['message'])

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
        self.assertEqual(data.get('total'), 1)
        added_num = data['numbers'][0]
        self.assertEqual(added_num['number'], '4915123456789')
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
            'body': 'Your code is 123456',
            'number': '79775594420',
            'app_id': 'whatsapp'
        }
        res_send = self.app.post('/api/admin/notifications/send', headers=headers, json=notify_payload)
        self.assertEqual(res_send.status_code, 200)
        self.assertTrue(res_send.get_json().get('success'))

    def test_admin_txt_file_upload(self):
        headers = {'X-Admin-Key': ADMIN_PASSWORD}
        # Mock .txt file with 3 phone numbers
        txt_content = b"14155552671\n14155552672\n14155552673\n"
        data = {
            'file': (io.BytesIO(txt_content), 'sample_numbers.txt'),
            'country': 'United States',
            'countryCode': '+1',
            'flag': '🇺🇸',
            'status': 'active',
            'supported_apps': '["whatsapp", "uber"]',
            'blocked_apps': '["paypal"]'
        }
        res = self.app.post('/api/admin/numbers/upload', headers=headers, data=data, content_type='multipart/form-data')
        self.assertEqual(res.status_code, 200)
        res_json = res.get_json()
        self.assertTrue(res_json.get('success'))
        self.assertIn('Added 3 numbers', res_json.get('message'))

        # Check that the numbers exist
        res_check = self.app.get('/api/numbers?search=14155552671')
        self.assertEqual(res_check.status_code, 200)
        numbers = res_check.get_json().get('numbers', [])
        self.assertEqual(len(numbers), 1)
        self.assertEqual(numbers[0]['country'], 'United States')
        self.assertIn('whatsapp', numbers[0]['supported_apps'])

        # Clean up added test numbers
        self.app.delete(f'/api/admin/numbers/{numbers[0]["id"]}', headers=headers)

if __name__ == '__main__':
    unittest.main()
