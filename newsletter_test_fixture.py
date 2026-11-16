"""Synthetic newsletter data for offline tests; never collect or publish."""
from newsletter import issue_hash

def fixture():
    x = {'schemaVersion':1, 'id':'daily-ai-2026-10-14', 'date':'2026-10-14', 'timezone':'Asia/Shanghai',
         'status':'approved','overview':{'paragraphs':['模拟今日总结'], 'topics':[]},
         'articles':[{'id':'fixture', 'category':'use', 'title':'模拟新闻', 'author':'Fixture',
                      'publishedAt':'2026-10-14', 'paragraphs':['模拟内容。'*60],
                      'sources':[{'label':'查看原文', 'url':'https://example.com/news'}]}]}
    x['approval'] = {'contentHash':issue_hash(x)}
    return x
