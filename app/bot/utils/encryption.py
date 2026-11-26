"""Password encryption utilities using Fernet (symmetric encryption)."""

import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class PasswordEncryption:
    """Handle password encryption/decryption using Fernet."""
    
    def __init__(self, encryption_key: Optional[str] = None):
        """
        Initialize encryption with a key.
        
        Args:
            encryption_key: Base64-encoded Fernet key. If None, uses ENCRYPTION_KEY env var.
        """
        if encryption_key is None:
            encryption_key = os.getenv("ENCRYPTION_KEY")
            
        # If no encryption key, use plaintext mode
        if not encryption_key:
            logger.warning("ENCRYPTION_KEY not set - using plaintext mode for passwords")
            self.fernet = None
            return
        
        try:
            self.fernet = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)
            logger.debug("Password encryption enabled")
        except Exception as e:
            logger.error(f"Invalid encryption key: {e}, falling back to plaintext mode")
            self.fernet = None
    
    def encrypt_password(self, password: str) -> str:
        """
        Encrypt a password.
        
        Args:
            password: Plain text password
            
        Returns:
            Encrypted password as base64 string (or plaintext if encryption disabled)
        """
        if not password:
            return ""
        
        # Plaintext mode
        if self.fernet is None:
            return password
        
        try:
            encrypted = self.fernet.encrypt(password.encode())
            return encrypted.decode()
        except Exception as e:
            logger.error(f"Failed to encrypt password: {e}")
            raise
    
    def decrypt_password(self, encrypted_password: str) -> str:
        """
        Decrypt a password.
        
        Args:
            encrypted_password: Encrypted password as base64 string (or plaintext)
            
        Returns:
            Decrypted plain text password
        """
        if not encrypted_password:
            return ""
        
        # Plaintext mode
        if self.fernet is None:
            return encrypted_password
        
        try:
            decrypted = self.fernet.decrypt(encrypted_password.encode())
            return decrypted.decode()
        except InvalidToken:
            # Password might be stored as plaintext - return as is
            logger.warning("Password appears to be plaintext, not encrypted - returning as is")
            return encrypted_password
        except Exception as e:
            logger.error(f"Failed to decrypt password: {e}")
            # Return plaintext as fallback
            return encrypted_password


# Global instance for easy access
_encryption_instance: Optional[PasswordEncryption] = None


def get_encryption() -> PasswordEncryption:
    """Get or create global encryption instance."""
    global _encryption_instance
    if _encryption_instance is None:
        _encryption_instance = PasswordEncryption()
    return _encryption_instance


def encrypt_password(password: str) -> str:
    """Convenience function to encrypt a password."""
    return get_encryption().encrypt_password(password)


def decrypt_password(encrypted_password: str) -> str:
    """Convenience function to decrypt a password."""
    return get_encryption().decrypt_password(encrypted_password)
