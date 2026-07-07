"""
Symmetric encryption for stored TOTP secrets.

Fernet with a server-side key from the FERNET_KEY env var (generate once with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
The secret is decrypted in memory only when verifying a code, never logged.
"""
import os

from cryptography.fernet import Fernet

_fernet = Fernet(os.environ["FERNET_KEY"].encode())


def encrypt(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()


def decrypt(encrypted: str) -> str:
    return _fernet.decrypt(encrypted.encode()).decode()
