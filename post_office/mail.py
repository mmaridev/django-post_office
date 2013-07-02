from multiprocessing import Process, Queue

from django.conf import settings
from django.core.mail import get_connection
from django.db.models import Q
from django.template import Context, Template

from .models import Email, EmailTemplate, PRIORITY, STATUS
from .settings import get_email_backend
from .utils import get_email_template, send_mail

try:
    from django.utils import timezone
    now = timezone.now
except ImportError:
    import datetime
    now = datetime.datetime.now


def from_template(sender, recipient, template, context={}, scheduled_time=None,
                  headers=None, priority=PRIORITY.medium):
    """Returns an Email instance from provided template and context."""
    # template can be an EmailTemplate instance of name
    if isinstance(template, EmailTemplate):
        template = template
    else:
        template = get_email_template(template)
    status = None if priority == PRIORITY.now else STATUS.queued
    context = Context(context)
    template_content = Template(template.content)
    template_content_html = Template(template.html_content)
    template_subject = Template(template.subject)
    return Email.objects.create(
        from_email=sender, to=recipient,
        subject=template_subject.render(context),
        message=template_content.render(context),
        html_message=template_content_html.render(context),
        scheduled_time=scheduled_time,
        headers=headers, priority=priority, status=status
    )


def send(recipients, sender=None, template=None, context={}, subject='',
         message='', html_message='', scheduled_time=None,
         headers=None, priority=PRIORITY.medium):

    if not isinstance(recipients, (tuple, list)):
        raise ValueError('Recipient emails must be in list/tuple format')

    if sender is None:
        sender = settings.DEFAULT_FROM_EMAIL

    if template:
        if subject:
            raise ValueError('You can\'t specify both "template" and "subject" arguments')
        if message:
            raise ValueError('You can\'t specify both "template" and "message" arguments')
        if html_message:
            raise ValueError('You can\'t specify both "template" and "html_message" arguments')

        emails = [from_template(sender, recipient, template, context, scheduled_time, headers, priority)
                  for recipient in recipients]
        if priority == PRIORITY.now:
            for email in emails:
                email.dispatch()
    else:
        if context:
            context = Context(context)
            subject = Template(subject).render(context)
            message = Template(message).render(context)
            html_message = Template(html_message).render(context)
        emails = send_mail(subject=subject, message=message, from_email=sender,
                           recipient_list=recipients, html_message=html_message,
                           scheduled_time=scheduled_time, headers=headers,
                           priority=priority)
    return emails


def send_queued():
    """
    Sends out all queued mails that has scheduled_time less than now or None
    """
    queued_emails = Email.objects.filter(status=STATUS.queued) \
        .filter(Q(scheduled_time__lte=now()) | Q(scheduled_time=None)) \
        .order_by('-priority')

    if queued_emails:
        result_tuple = _send_bulk(queued_emails)

    print '%s emails attempted, %s sent, %s failed' % (
        len(queued_emails),
        result_tuple[0],
        result_tuple[1]
    )


def _send_bulk(emails):
    sent_count = 0
    failed_count = 0

    # Try to open a connection, if we can't just pass in None as connection
    try:
        connection = get_connection(get_email_backend())
        connection.open()
    except Exception:
        connection = None

    for email in emails:
        status = email.dispatch(connection)
        if status == STATUS.sent:
            sent_count += 1
        else:
            failed_count += 1
    if connection:
        connection.close()

    return (sent_count, failed_count)