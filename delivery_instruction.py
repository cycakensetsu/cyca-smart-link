"""Conservative address extraction only; never authorizes or sends a message."""
import re

_ERROR = '宛先を特定できません。推測せず、メールアドレスを1件だけ入力してください。'
_TOKEN = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+')


def extract_recipient(instruction):
    """Return one explicitly written, supported ASCII email address.

    Names, multiple occurrences (including quoted copies), quoted local parts,
    malformed or split addresses, and mail-header injection are not inferred.
    Japanese prose and normal quote/bracket delimiters are permitted.
    """
    if (not isinstance(instruction,str) or not instruction.strip() or len(instruction)>4000
            or instruction.count('@') != 1 or '＠' in instruction
            or any(ord(c)<32 and c not in '\r\n\t' for c in instruction)
            or re.search(r'(?im)^\s*(?:cc|bcc|from|reply-to|content-type)\s*:',instruction)):
        raise ValueError(_ERROR)
    matches=list(_TOKEN.finditer(instruction))
    if len(matches)!=1: raise ValueError(_ERROR)
    match=matches[0]; value=match.group()
    before=instruction[match.start()-1:match.start()] if match.start() else ''
    after=instruction[match.end():match.end()+1]
    # Do not salvage a valid-looking substring of an invalid address or URL.
    # Explicit delimiters only. Never trim Unicode letters, combining marks,
    # zero-width format characters, or Japanese prose off an address token.
    before_delimiters = '<(["\':「『【（［〈《：'
    after_delimiters = '>)]"\',;!」』】）］〉》、。！，；'
    if before and not (before in ' \t\r\n' or before in before_delimiters):
        raise ValueError(_ERROR)
    if after and not (after in ' \t\r\n' or after in after_delimiters):
        raise ValueError(_ERROR)
    local,domain=value.split('@')
    if (len(value)>254 or len(local)>64 or local.startswith('.') or local.endswith('.') or '..' in local
            or not re.fullmatch(r'[A-Za-z0-9]+(?:[A-Za-z0-9._%+\-]*[A-Za-z0-9])?',local)):
        raise ValueError(_ERROR)
    labels=domain.split('.')
    if (len(labels)<2 or not re.fullmatch('[A-Za-z]{2,63}',labels[-1])
            or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?',label) for label in labels)):
        raise ValueError(_ERROR)
    return value
