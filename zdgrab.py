"""
zdgrab: Download attachments from Zendesk tickets
"""


import os
import sys
import re
import textwrap
import base64
import subprocess
import json

import zdeskcfg
from zdesk import Zendesk

from asplode import asplode


try:
    from ssgrab import ssgrab
    ss_present = True
except ModuleNotFoundError:
    ss_present = False


class verbose_printer:
    def __init__(self, v):
        if v:
            self.print = self.verbose_print
        else:
            self.print = self.null_print

    def verbose_print(self, msg, end='\n'):
        print(msg, file=sys.stderr, end=end)

    def null_print(self, msg, end='\n'):
        pass


@zdeskcfg.configure(
    verbose=('verbose output', 'flag', 'v'),
    tickets=('Ticket(s) to grab attachments (default: all of your open tickets)',
             'option', 't', str, None, 'TICKETS'),
    count=('Retrieve up to this many attachments (default: 0, all)',
           'option', 'c', int, None, 'COUNT'),
    work_dir=('Working directory in which to store attachments. (default: ~/zdgrab)',
              'option', 'w', str, None, 'WORK_DIR'),
    agent=('Agent whose open tickets to search (default: me)',
           'option', 'a', str, None, 'AGENT'),
    product=('Product to search', 'option', 'p', str, None, 'PRODUCT'),
    ss_host=('SendSafely host to connect to, including protocol',
             'option', None, str, None, 'SS_HOST'),
    ss_id=('SendSafely API key', 'option', None, str, None, 'SS_ID'),
    ss_secret=('SendSafely API secret',
               'option', None, str, None, 'SS_SECRET'),
    extract=('Automatically extract archives',
             'flag', 'x', bool, None, 'EXTRACT'),
    dryrun=('Dry run to test without downloading',
            'flag', 'd', bool, None, 'DRYRUN'),
)
def _zdgrab(verbose=False,
            tickets=None,
            count=0,
            work_dir=os.path.join(os.path.expanduser('~'), 'zdgrab'),
            agent='me',
            product='nomad',
            ss_host=None,
            ss_id=None,
            ss_secret=None,
            extract=False,
            dryrun=False):
    "Download attachments from Zendesk tickets."

    cfg = _zdgrab.getconfig()

    zdgrab(verbose=verbose,
           tickets=tickets,
           count=count,
           work_dir=work_dir,
           agent=agent,
           product=product,
           ss_host=ss_host,
           ss_id=ss_id,
           ss_secret=ss_secret,
           zdesk_cfg=cfg,
           extract=extract,
           dryrun=dryrun)


def zdgrab(verbose, tickets, count, work_dir, agent, product, ss_host, ss_id, ss_secret,
           zdesk_cfg, extract, dryrun):
    # ssgrab will only be invoked if the comment body contains a link.
    # See the corresponding REGEX used by them, which has been ported to Python:
    # https://github.com/SendSafely/Windows-Client-API/blob/master/SendsafelyAPI/Utilities/ParseLinksUtility.cs
    ss_link_re = r'https://[-a-zA-Z\.]+/receive/\?[-A-Za-z0-9&=]+packageCode=[-A-Za-z0-9_]+#keyCode=[-A-Za-z0-9_]+'
    ss_link_pat = re.compile(ss_link_re)

    vp = verbose_printer(verbose)

    if zdesk_cfg.get('zdesk_url') and (
            zdesk_cfg.get('zdesk_oauth') or
            (zdesk_cfg.get('zdesk_email') and zdesk_cfg.get('zdesk_password')) or
            (zdesk_cfg.get('zdesk_email') and zdesk_cfg.get('zdesk_api'))
    ):
        vp.print(f'Configuring Zendesk with:\n'
                 f'  url: {zdesk_cfg.get("zdesk_url")}\n'
                 f'  email: {zdesk_cfg.get("zdesk_email")}\n'
                 f'  token: {repr(zdesk_cfg.get("zdesk_token"))}\n'
                 f'  password/oauth/api: (hidden)\n')

        zd = Zendesk(**zdesk_cfg)
    else:
        msg = textwrap.dedent("""\
            Error: Need Zendesk config to continue.

            Config file (~/.zdeskcfg) should be something like:
            [zdesk]
            url = https://example.zendesk.com
            email = you@example.com
            api = dneib393fwEF3ifbsEXAMPLEdhb93dw343
            # or
            # oauth = ndei393bEwF3ifbEsssX

            [zdgrab]
            agent = agent@example.com
            """)
        print(msg)
        return 1

    # Log the cfg
    vp.print(f'Running with zdgrab config:\n'
             f' verbose: {verbose}\n'
             f' tickets: {tickets}\n'
             f' count: {count}\n'
             f' work_dir: {work_dir}\n'
             f' agent: {agent}\n'
             f' product: {product}\n'
             f' extract: {extract}\n'
             f' dryrun: {dryrun}\n')

    # tickets=None means default to getting all of the attachments for this
    # user's open tickets. If tickets is given, try to split it into ints
    if tickets:
        # User gave a list of tickets
        try:
            tickets = [int(i) for i in tickets.split(',')]
        except ValueError:
            print(f'Error: Could not convert to integers: {tickets}')
            return 1

    # dict of paths to attachments retrieved to return. format is:
    # { 'path/to/ticket/1': [ 'path/to/attachment1', 'path/to/attachment2' ],
    #   'path/to/ticket/2': [ 'path/to/attachment1', 'path/to/attachment2' ] }
    grabs = {}

    # Save the current directory so we can go back once done
    start_dir = os.getcwd()

    # Normalize all of the given paths to absolute paths
    work_dir = os.path.abspath(work_dir)

    # Check for and create working directory
    if not os.path.isdir(work_dir):
        os.makedirs(work_dir)

    # Change to working directory to begin file output
    os.chdir(work_dir)

    if tickets:
        # tickets given, query for those
        print(f'Retrieving ticket id(s) {tickets}')
        response = zd.tickets_show_many(ids=','.join([s for s in map(str, tickets)]),
                                        get_all_pages=True)
        result_field = 'tickets'
    elif product:
        # Product given, get all tickets for product
        print(f'Retrieving open tickets for product = {product}')
        q = f'status<solved product:{product}'
        response = zd.search(query=q, get_all_pages=True)
        result_field = 'results'
    else:
        # List of tickets not given. Get all of the attachments for all of this
        # user's open tickets.
        print(f'Retrieving ticket(s) for agent {agent}')
        q = f'status<solved assignee:{agent}'
        response = zd.search(query=q, get_all_pages=True)
        result_field = 'results'

    if response['count'] == 0:
        # No tickets from which to get attachments
        print("No tickets provided for attachment retrieval.")
        return {}
    else:
        print(f'\t{response["count"]} tickets found')

    results = response[result_field]

    # Fix up some headers to use for downloading the attachments.
    # We're going to borrow the zdesk object's httplib client.
    headers = {}
    if zd.zdesk_email is not None and zd.zdesk_password is not None:
        basic = base64.b64encode(zd.zdesk_email.encode('ascii') +
                                 b':' + zd.zdesk_password.encode('ascii'))
        headers["Authorization"] = f"Basic {basic}"

    # Get the attachments from the given zendesk tickets
    for ticket in results:
        if result_field == 'results' and ticket['result_type'] != 'ticket':
            # This is not actually a ticket. Weird. Skip it.
            continue

        print("\n------------------------------------------------------------\n")
        print(f'{ticket["id"]} - {ticket["subject"]}')

        ticket_dir = os.path.join(work_dir, str(ticket['id']))
        ticket_com_dir = os.path.join(ticket_dir, 'comments')
        comment_num = 0
        attach_num = 0

        if dryrun == True:
            continue

        # Ensure ticket directory exists
        if not os.path.isdir(ticket_dir):
            os.makedirs(ticket_dir)

        # Write ticket JSON to file
        ticket_json_filename = os.path.join(
            ticket_dir,
            f'{ticket["id"]}-meta.json')
        with open(ticket_json_filename, 'w') as fp:
            json.dump(ticket, fp, indent=4, sort_keys=True)

        # Write ticket summary to file
        ticket_summary_filename = os.path.join(
            ticket_dir,
            f'{ticket["id"]}-summary.txt')
        with open(ticket_summary_filename, 'w') as f:
            f.write(ticket["description"])

        response = zd.ticket_audits(ticket_id=ticket['id'],
                                    get_all_pages=True)

        audits = response['audits'][::-1]
        audit_num = len(audits) + 1

        for audit in audits:
            audit_num -= 1
            for event in audit['events']:
                if event['type'] != 'Comment':
                    # This event isn't a comment. Skip it.
                    continue

                comment_num = audit_num
                comment_dir = os.path.join(ticket_com_dir, str(comment_num))

                if count > 0 and attach_num >= count:
                    break

                for attachment in event['attachments']:
                    attach_num += 1

                    if count > 0:
                        attach_msg = f' ({attach_num}/{count})'
                    else:
                        attach_msg = f' ({attach_num})'

                    name = attachment['file_name']
                    if os.path.isfile(os.path.join(comment_dir, name)):
                        vp.print(
                            f' Attachment {name} already present{attach_msg}')
                        continue

                    # Get this attachment
                    vp.print(f' Downloading attachment {name}{attach_msg}')

                    # Check for and create the download directory
                    if not os.path.isdir(comment_dir):
                        os.makedirs(comment_dir)

                    os.chdir(comment_dir)
                    response = zd.client.request('GET',
                                                 attachment['content_url'],
                                                 headers=headers)

                    if response.status_code != 200:
                        print(f'Error downloading {attachment["content_url"]}')
                        continue

                    with open(name, 'wb') as f:
                        f.write(response.content)

                    # Check for and create the grabs entry to return
                    if ticket_dir not in grabs:
                        grabs[ticket_dir] = []

                    grabs[ticket_dir].append(
                        os.path.join('comments', str(comment_num), name))

                    # Let's try to extract this if it's compressed
                    asplode(name, verbose=verbose)

                if not ss_present:
                    continue

                for link in ss_link_pat.findall(event['body']):
                    attach_num += 1

                    if count > 0:
                        attach_msg = f' ({attach_num}/{count})'
                    else:
                        attach_msg = f' ({attach_num})'

                    ss_files = ssgrab(verbose=verbose, key=ss_id, secret=ss_secret,
                                      host=ss_host, link=link, work_dir=comment_dir,
                                      postmsg=attach_msg)

                    # Check for and create the grabs entry to return
                    if ss_files and (ticket_dir not in grabs):
                        grabs[ticket_dir] = []

                    for name in ss_files:
                        grabs[ticket_dir].append(
                            os.path.join('comments', str(comment_num), name))

                        if extract:
                            # Let's try to extract this if it's compressed
                            os.chdir(comment_dir)
                            asplode(name, verbose=verbose)

    print("\n------------------------------------------------------------\n")

    os.chdir(start_dir)
    return grabs


def main(argv=None):
    zdeskcfg.call(_zdgrab, section='zdgrab')
