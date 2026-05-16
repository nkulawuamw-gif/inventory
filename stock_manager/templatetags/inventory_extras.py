from django import template

register = template.Library()


@register.filter
def dict_lookup(d, key):
    return d.get(key.lower())
