"""SMTP transaction with an explicit pre-DATA / possibly-delivered boundary."""
import smtplib
import ssl


class Rejected(Exception):
    def __init__(self,code):
        self.code=code
        super().__init__(f'SMTP rejected request ({code})')


class SMTPChannel:
    def __init__(self,settings): self.settings=settings;self.connection=None

    def prepare(self,envelope):
        s=self.settings
        try:
            if s.smtp_security=='ssl':
                self.connection=smtplib.SMTP_SSL(s.smtp_host,s.smtp_port,timeout=s.smtp_timeout,context=ssl.create_default_context())
            else:
                self.connection=smtplib.SMTP(s.smtp_host,s.smtp_port,timeout=s.smtp_timeout)
                self.connection.ehlo()
                self.connection.starttls(context=ssl.create_default_context())
            self.connection.ehlo()
            if s.smtp_username: self.connection.login(s.smtp_username,s.smtp_password.get_secret_value())
            code,_=self.connection.mail(envelope['sender'])
            if code!=250: raise Rejected(code)
            for address in envelope['recipients']:
                code,_=self.connection.rcpt(address)
                if code not in (250,251): raise Rejected(code)
        except BaseException:
            self.close();raise

    def deliver(self,mime):
        # smtplib.data handles dot stuffing. A disconnect during this call has an
        # unknown delivery outcome, even if the client never sees the final 250.
        code,_=self.connection.data(mime)
        if code!=250: raise Rejected(code)

    def close(self):
        if self.connection:
            self.connection.close();self.connection=None
