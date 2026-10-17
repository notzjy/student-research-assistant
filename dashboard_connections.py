"""手机无线接入与重点收件箱界面，业务操作仍由已有HTTP后端完成。"""

from io import BytesIO
from urllib.parse import urlencode, urlsplit

import qrcode
import streamlit as st

from dashboard_api import DashboardError


def api_request(api, method, path, **kwargs):
    """统一显示安全接口错误，不把令牌或原始HTTP异常写进界面提示。"""
    try:
        return api.request(method, path, **kwargs)
    except DashboardError as error:
        st.error(str(error))
        if error.trace_id:
            st.caption('Trace ID: ' + error.trace_id)
        return None


def qr_bytes(text):
    """仅在本机生成二维码，不把手机配对令牌发送给任何第三方服务。"""
    stream = BytesIO()
    qrcode.make(text).save(stream, format='PNG')
    return stream.getvalue()


def render_sender_rule(api, message):
    """从用户选中的真实来源消息填写角色键，角色名称与重要程度必须由用户确定。"""
    source = message.get('source_message') or {}
    if not all(source.get(key) for key in ('source', 'account_id', 'sender_id')):
        return
    notification_scope = (message.get('source_metadata') or {}).get('sender_identity_verified') is False
    with st.expander('设置此通知会话的角色与重要程度' if notification_scope else '设置这个发送者的角色与重要程度'):
        if notification_scope:
            st.warning('这条来自系统通知；匹配范围是此设备、应用与会话标识，通知标题不能保证是真实发件人的唯一身份。')
        st.text('来源：' + source['source'] + '；账号：' + source['account_id'] + '；发送者标识：' + source['sender_id'])
        current = message.get('role_match') or {}
        with st.form('sender_rule_' + message['message_id']):
            role = st.text_input('你给此联系人的角色', value=current.get('role', ''), key='sender_role_' + message['message_id'])
            important = st.checkbox('收到此联系人的新消息时重点标记', value=current.get('important', True), key='sender_important_' + message['message_id'])
            save = st.form_submit_button('保存此发送者角色')
        if save:
            if not role.strip():
                st.error('请填写你确定的角色名称。')
            else:
                result = api_request(api, 'PUT', '/roles', json={key: source[key] for key in ('source', 'account_id', 'sender_id')} | {'role': role.strip(), 'important': important})
                if result:
                    st.success('角色已保存，后续消息按此规则识别；不会为历史消息补发到达弹窗。')


def attention_page(api):
    """展示重点联系人新消息并提供独立弹窗规则和明确已读动作。"""
    st.header('重点收件箱')
    st.caption('重点联系人到达提醒与截止提醒分别设置。提交到Windows不代表你已读；历史消息不会补建到达提醒。')
    policy = api_request(api, 'GET', '/attention/policy') or {}
    with st.form('attention_policy'):
        enabled = st.checkbox('重点联系人新消息弹窗与提示音', value=policy.get('enabled', False), key='attention_enabled')
        age = st.number_input('超过多少分钟前发出的消息不弹到达提醒', min_value=1, max_value=1440, value=policy.get('max_age_minutes', 60))
        channel = st.selectbox('到达提醒通道', ['windows', 'console'], index=0 if policy.get('channel', 'windows') == 'windows' else 1)
        save = st.form_submit_button('保存重点消息提醒规则')
    if save and api_request(api, 'PUT', '/attention/policy', json={'enabled': enabled, 'max_age_minutes': int(age), 'channel': channel}):
        st.success('重点消息提醒规则已保存。')
    result = api_request(api, 'GET', '/attention') or {}
    alerts = result.get('alerts', [])
    if not alerts:
        st.info('尚无新的重点联系人到达记录。先在消息卡片中明确设置角色，再接收该联系人的新消息。')
    for item in alerts:
        with st.container(border=True):
            st.badge('重点消息', color='orange')
            st.text(item['title'])
            st.caption('状态：' + item['status'] + '；消息编号：' + item['message_id'])
            if item['status'] != 'read' and st.button('标记已读', key='attention_read_' + item['alert_id']):
                if api_request(api, 'POST', '/attention/' + item['alert_id'] + '/read'):
                    st.success('已读。')


def phone_page(api):
    """引导无线安装与配对，只在明确操作后生成或轮换专用通知令牌。"""
    st.header('手机接入')
    st.text('手机通过网络上传微信、QQ、BOSS直聘的系统通知。日常不需要USB，断连先缓存，恢复网络后补传。通知可能是摘要，不能保证包含聊天全文或文件原件。')
    status = api_request(api, 'GET', '/notifications/status') or {}
    if status.get('listen_host') != '0.0.0.0':
        st.warning('当前主服务只监听本机。请在启动器勾选“允许手机通过局域网接入”后重新启动；也可以使用原数据库路径并加 --host 0.0.0.0 启动后端。')
    st.json(status)
    with st.form('bridge_setup'):
        enabled = st.checkbox('启用手机通知接入', value=status.get('enabled', False), key='phone_enabled')
        rotate = st.checkbox('重新生成令牌（旧手机配置会失效）', key='phone_rotate')
        trust_posted = st.checkbox('无原消息时间时，允许以通知发布时间计算相对日期并自动提醒', value=status.get('trust_posted_time', False), key='phone_trust_posted_time')
        save = st.form_submit_button('保存并显示本机配对信息')
    if save:
        result = api_request(api, 'POST', '/notifications/config', json={'enabled': enabled, 'rotate': rotate, 'trust_posted_time': trust_posted})
        if result:
            # 令牌只留在本地界面会话，不存业务消息、Trace或第三方二维码站点。
            st.session_state['phone_pairing'] = result
            status['enabled'] = result['enabled']
    if status.get('enabled') is not True:
        st.session_state.pop('phone_pairing', None)
        st.session_state.pop('phone_apk', None)
    pairing = st.session_state.get('phone_pairing')
    if not pairing:
        st.info('启用后显示下载安装包和配对二维码。手机与电脑可先连接同一Wi-Fi。')
        return
    urls = pairing.get('upload_urls') or []
    address = st.selectbox('手机能访问的电脑地址', urls) if urls else st.text_input('上传地址，例如 http://电脑局域网IP:8002/notifications')
    external = st.text_input('跨网络HTTPS地址（可选，仅在你已配置安全可达的服务器入口时填写）', key='phone_external_url')
    if external.strip():
        parsed = urlsplit(external.strip())
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip('/') not in ('', '/notifications'):
            st.error('跨网络入口应为不含账号或额外参数的HTTPS地址，例如 https://你的域名/notifications。')
            return
        address = external.strip().rstrip('/')
        if not address.endswith('/notifications'):
            address += '/notifications'
    if address:
        apk_url = address.removesuffix('/notifications') + '/notifications/app.apk'
        st.text('第一步：手机浏览器打开下面地址，下载并安装本项目通知助手。')
        st.code(apk_url, language=None)
        st.image(qr_bytes(apk_url), caption='扫码在手机浏览器下载安装包', width=220)
        st.text('第二步：安装后用手机扫码下方配对码，确认保存，再在安卓系统设置中授权此应用读取通知。')
        deep_link = 'student-assistant://pair?' + urlencode({'server': address, 'token': pairing['token']})
        st.image(qr_bytes(deep_link), caption='私有配对码，仅供你的手机扫描', width=260)
        with st.expander('手工填写配对信息'):
            st.code(address, language=None)
            st.text_input('专用接入令牌', value=pairing['token'], type='password')
        st.caption('离开当前Wi-Fi后，跨网络实时上传需要可安全访问的服务器地址；未配置时缓存等待重连。不要把配对码、令牌或电脑管理界面公开到互联网。')
    if st.button('准备下载安卓安装包'):
        try:
            st.session_state['phone_apk'] = api.download_phone_app()
        except DashboardError as error:
            st.error(str(error))
    if 'phone_apk' in st.session_state:
        st.download_button('下载安卓APK到电脑', st.session_state['phone_apk'], file_name='student-assistant-bridge.apk', mime='application/vnd.android.package-archive')
