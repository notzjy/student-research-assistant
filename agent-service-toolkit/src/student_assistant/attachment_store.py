"""附件原件归档：任何格式均保留可下载字节，是否可提取正文与是否取得文件分别记录。"""

import hashlib
import os
from pathlib import Path
from uuid import uuid4


class AttachmentStore:
    """按消息附件序号绑定原件，受管文件使用内容哈希命名，不执行文件内容。"""

    def __init__(self, message_store, root_dir):
        """复用业务库建立附件原件表，目录位于当前业务数据的独立运行路径。"""
        self.messages = message_store
        self.root = Path(root_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.messages._connection(write=True) as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS attachment_originals (
                message_id TEXT NOT NULL, attachment_index INTEGER NOT NULL,
                filename TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                media_type TEXT, PRIMARY KEY(message_id,attachment_index),
                FOREIGN KEY(message_id) REFERENCES messages(message_id))''')

    def save(self, message_id, attachment_index, filename, data, media_type=None):
        """先校验消息已有附件线索，再原子保留真实字节；同一序号不同内容拒绝覆盖。"""
        if not isinstance(data, bytes) or len(data) > 25 * 1024 * 1024:
            raise ValueError('附件字节不能超过25MB')
        if type(attachment_index) is not int or attachment_index < 0:
            raise ValueError('附件序号无效')
        digest = hashlib.sha256(data).hexdigest()
        with self.messages._connection(write=True) as connection:
            hint = connection.execute('SELECT hint_json FROM attachments WHERE message_id=? AND attachment_index=?', (message_id, attachment_index)).fetchone()
            if hint is None:
                raise ValueError('消息没有该附件线索')
            previous = connection.execute('SELECT * FROM attachment_originals WHERE message_id=? AND attachment_index=?', (message_id, attachment_index)).fetchone()
            if previous and previous['sha256'] != digest:
                raise ValueError('该附件已有其他原件，请使用新的消息或附件序号')
            path = self.root / digest
            temporary = None
            try:
                if not path.exists():
                    temporary = self.root / (uuid4().hex + '.tmp')
                    temporary.write_bytes(data)
                    os.replace(temporary, path)
                connection.execute('INSERT OR IGNORE INTO attachment_originals VALUES(?,?,?,?,?,?)', (message_id, attachment_index, filename[:255], digest, len(data), media_type))
                row = connection.execute('SELECT * FROM attachment_originals WHERE message_id=? AND attachment_index=?', (message_id, attachment_index)).fetchone()
                return dict(row)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)

    def list(self, message_id):
        """读取某条消息已取得的原件元数据，不读取正文或调用模型。"""
        with self.messages._connection() as connection:
            return [dict(row) for row in connection.execute('SELECT * FROM attachment_originals WHERE message_id=? ORDER BY attachment_index', (message_id,))]

    def read(self, message_id, attachment_index):
        """按消息与附件序号读取原件，校验哈希后返回元数据和字节。"""
        with self.messages._connection() as connection:
            row = connection.execute('SELECT * FROM attachment_originals WHERE message_id=? AND attachment_index=?', (message_id, attachment_index)).fetchone()
        if row is None:
            return None
        data = (self.root / row['sha256']).read_bytes()
        if hashlib.sha256(data).hexdigest() != row['sha256']:
            raise OSError('附件原件未通过完整性校验')
        return dict(row), data

    def overlay(self, detail):
        """在消息详情叠加真实原件状态，保留原始mentioned并明确正文是否已建立索引。"""
        import copy
        result = copy.deepcopy(detail)
        for original in self.list(detail['message_id']):
            item = result['attachments'][original['attachment_index']]
            item.update({'original_available': True, 'size_bytes': original['size_bytes'], 'sha256': original['sha256']})
            if item.get('availability') != 'indexed':
                item['availability'] = 'available'
        return result
