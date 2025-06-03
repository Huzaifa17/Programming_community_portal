import re

def make_links_clickable(text):
    """
    Convert URLs in text to clickable links (colored blue and opening in a new tab).
    Regular text remains black.
    """
    url_pattern = re.compile(r'https?://\S+')
    return url_pattern.sub(r'<a href="\g<0>" target="_blank" style="color: blue;">\g<0></a>', text)