import analytics

r = analytics.answer()
assert isinstance(r, (int, float)) and not isinstance(r, bool), r
assert r >= 0, r
print('visible test ok')
