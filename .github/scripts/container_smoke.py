"""GitHub临时运行器上的实际Compose验收，不注入用户凭证或发送任何通知。"""

import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request


def request(path, method='GET', payload=None):
    """只请求运行器回环API，将测试配置作为JSON发送，不调用模型或真实邮箱。"""
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    item = urllib.request.Request('http://127.0.0.1:8002' + path, data=body, method=method, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(item, timeout=5) as response:
        data = response.read()
        return json.loads(data) if 'application/json' in response.headers.get('Content-Type', '') else data


def wait_ready(url):
    """等待单个容器的HTTP就绪；固定最长90秒，不把进程存在当作服务已成功。"""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise TimeoutError('容器HTTP服务未在预算内就绪')


def main():
    """验证正式镜像双服务、原件安装包、空凭证边界及后端重启后的业务持久性。"""
    wait_ready('http://127.0.0.1:8002/health')
    wait_ready('http://127.0.0.1:8501/_stcore/health')
    assert request('/info')['profile'] == 'student'
    role = {'source': 'manual', 'account_id': 'container-smoke', 'sender_id': 'test-contact', 'role': '验收联系人', 'important': True}
    assert request('/roles', 'PUT', role)['role'] == role['role']
    status = request('/mail/status')
    assert not status['enabled'] and not status['configured']
    with open('android-notification-bridge/dist/student-assistant-bridge.apk', 'rb') as file:
        expected = hashlib.sha256(file.read()).digest()
    assert hashlib.sha256(request('/notifications/app.apk')).digest() == expected
    subprocess.run(['docker', 'compose', 'restart', 'backend'], check=True)
    wait_ready('http://127.0.0.1:8002/health')
    roles = request('/roles')
    assert any(item['account_id'] == role['account_id'] and item['role'] == role['role'] for item in roles['roles'])
    print(json.dumps({'event': 'container_smoke_passed', 'checks': ['actual_image_build', 'actual_compose_backend_and_ui_http', 'downloaded_apk_hash_matches', 'no_model_or_mail_credentials', 'sqlite_role_persists_after_container_restart'], 'real_model_calls': False, 'windows_notifications': False}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
