from django import template

register = template.Library()


@register.filter
def lookup(d, key):
    if d is None:
        return None
    return d.get(key)


@register.filter
def sum_field(data, field):
    total = 0
    for item in data:
        total += item.get(field, 0)
    return total


@register.filter
def divide(value, arg):
    try:
        return float(value) / float(arg)
    except (ValueError, ZeroDivisionError):
        return 0
