import ssl
import smtplib
from django.core.mail.backends.smtp import EmailBackend


class CustomSSLEmailBackend(EmailBackend):
    """
    Custom email backend that bypasses SSL certificate verification.
    Use only if you trust the email server.
    """
    
    def open(self):
        if self.connection:
            return False
        
        try:
            # Create SSL context that doesn't verify certificates
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            
            if self.use_ssl:
                self.connection = smtplib.SMTP_SSL(
                    self.host,
                    self.port,
                    timeout=self.timeout,
                    context=context
                )
            else:
                self.connection = smtplib.SMTP(
                    self.host,
                    self.port,
                    timeout=self.timeout
                )
                
                if self.use_tls:
                    self.connection.starttls(context=context)
            
            if self.username and self.password:
                self.connection.login(self.username, self.password)
            
            return True
            
        except smtplib.SMTPException:
            if not self.fail_silently:
                raise
            return False