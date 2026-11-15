"""Local at-rest encryption for SSH passwords submitted through host settings."""
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from srs.transport import TransportError


class CredentialVault:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'ssh-credentials.key'

    def cipher(self, *, create=False):
        if create:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    raise TransportError('SSH 憑證金鑰檔必須由服務帳號持有，且權限為 0600')
                key = stream.read(128)
            return Fernet(key)
        except (OSError, ValueError) as exc:
            raise TransportError('無法讀取 SSH 憑證金鑰，請保留並還原原本的 ssh-credentials.key') from exc

    def encrypt(self, password):
        return self.cipher(create=True).encrypt(password.encode()).decode()

    def decrypt(self, ciphertext):
        try:
            return self.cipher().decrypt(ciphertext.encode()).decode()
        except (InvalidToken, UnicodeError) as exc:
            raise TransportError('無法解密此主機的 SSH 密碼，請重新儲存密碼或還原原本金鑰') from exc
