"""
Encryption Service - Secure storage of API keys and sensitive data.
"""

import os
import base64
from typing import Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class EncryptionService:
    """
    Service for encrypting and decrypting sensitive data.
    Uses Fernet symmetric encryption with a key derived from environment secret.
    """
    
    def __init__(self):
        self._fernet: Optional[Fernet] = None
        self._key: Optional[bytes] = None
    
    def _get_secret_key(self) -> bytes:
        """Get or generate the secret key."""
        secret = os.getenv("ENCRYPTION_SECRET")
        
        if not secret:
            # Generate a new secret if not set
            secret = base64.urlsafe_b64encode(os.urandom(32)).decode()
            # In production, this should be set in .env
            print(f"WARNING: No ENCRYPTION_SECRET set. Generated: {secret}")
            print("Please add this to your .env file for persistent encryption.")
        
        # Derive a key from the secret using PBKDF2
        salt = b'local_ai_chatbot_console_v1'  # Fixed salt for consistency
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        
        return base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    
    @property
    def fernet(self) -> Fernet:
        """Get or create Fernet instance."""
        if self._fernet is None:
            self._key = self._get_secret_key()
            self._fernet = Fernet(self._key)
        return self._fernet
    
    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a string.
        
        Args:
            plaintext: String to encrypt
            
        Returns:
            Base64-encoded encrypted string
        """
        if not plaintext:
            return ""
        
        encrypted = self.fernet.encrypt(plaintext.encode())
        return base64.urlsafe_b64encode(encrypted).decode()
    
    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt a string.
        
        Args:
            ciphertext: Base64-encoded encrypted string
            
        Returns:
            Decrypted plaintext string
        """
        if not ciphertext:
            return ""
        
        try:
            encrypted = base64.urlsafe_b64decode(ciphertext.encode())
            decrypted = self.fernet.decrypt(encrypted)
            return decrypted.decode()
        except Exception as e:
            raise ValueError(f"Failed to decrypt: {str(e)}")
    
    def is_encrypted(self, value: str) -> bool:
        """
        Check if a value appears to be encrypted.
        
        Args:
            value: String to check
            
        Returns:
            True if appears encrypted
        """
        if not value:
            return False
        
        try:
            # Try to decode as base64
            decoded = base64.urlsafe_b64decode(value.encode())
            # Fernet tokens start with 'gAAAAA'
            return decoded.startswith(b'gAAAAA') or len(decoded) > 50
        except Exception:
            return False


# Singleton instance
encryption_service = EncryptionService()
