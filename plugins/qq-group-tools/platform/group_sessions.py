"""QQ members own separate named conversations; quote anchors preserve topic routing."""
from contextlib import closing
import hashlib
import re
import sqlite3
import time
from .group_mentions import reply_reference

DEFAULT_TOPIC = "默认"

class TopicStore:
    def __init__(self,path):
        self.path=path

    def connect(self):
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        c=sqlite3.connect(self.path,timeout=10)
        c.executescript("""
            CREATE TABLE IF NOT EXISTS topics(chat TEXT,user TEXT,label TEXT,updated REAL,PRIMARY KEY(chat,user,label));
            CREATE TABLE IF NOT EXISTS active(chat TEXT,user TEXT,label TEXT,PRIMARY KEY(chat,user));
            CREATE TABLE IF NOT EXISTS incoming(id TEXT PRIMARY KEY,chat TEXT,user TEXT,label TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS replies(chat TEXT,reference TEXT,user TEXT,label TEXT,created REAL,PRIMARY KEY(chat,reference));
        """)
        c.commit()
        self.path.chmod(0o600)
        return c

    def select(self,chat,user,label):
        if not label or len(label)>40 or any(ord(c)<32 for c in label):
            raise ValueError("话题名需要 1~40 字")
        with closing(self.connect()) as c,c:
            c.execute("INSERT OR REPLACE INTO topics VALUES (?,?,?,?)",(chat,user,label,time.time()))
            c.execute("INSERT OR REPLACE INTO active VALUES (?,?,?)",(chat,user,label))

    def current(self,chat,user,payload=None):
        with closing(self.connect()) as c:
            ref=reply_reference(payload or {})
            row=c.execute("SELECT label FROM replies WHERE chat=? AND reference=? AND user=?",(chat,ref,user)).fetchone() if ref else None
            row=row or c.execute("SELECT label FROM active WHERE chat=? AND user=?",(chat,user)).fetchone()
        return row[0] if row else DEFAULT_TOPIC

    def labels(self,chat,user):
        with closing(self.connect()) as c:
            rows=c.execute("SELECT label FROM topics WHERE chat=? AND user=? ORDER BY updated DESC",(chat,user)).fetchall()
        return [r[0] for r in rows] or [DEFAULT_TOPIC]

    def bind_incoming(self,message_id,chat,user,label):
        with closing(self.connect()) as c,c:
            c.execute("INSERT OR REPLACE INTO incoming VALUES (?,?,?,?,?)",(message_id,chat,user,label,time.time()))
            c.execute("DELETE FROM incoming WHERE created<?",(time.time()-7*86400,))
            c.execute("DELETE FROM replies WHERE created<?",(time.time()-30*86400,))

    def remember_reply(self,path,body,response):
        match=re.fullmatch(r"/v2/groups/([^/]+)/messages",path)
        if not match or not body.get("msg_id"):
            return
        with closing(self.connect()) as c,c:
            row=c.execute("SELECT user,label FROM incoming WHERE id=? AND chat=?",(body["msg_id"],match[1])).fetchone()
            if not row:
                return
            refs=[response.get("id"),(response.get("ext_info") or {}).get("ref_idx")]
            for ref in refs:
                if ref:
                    c.execute("INSERT OR REPLACE INTO replies VALUES (?,?,?,?,?)",(match[1],str(ref),*row,time.time()))

def thread_id(label):
    return None if label==DEFAULT_TOPIC else "qq-topic-"+hashlib.sha256(label.encode()).hexdigest()[:16]

async def topic_command(adapter,chat,user,text):
    text=re.sub(r"^大肥鱼[，,:：\s]*","",text.strip()).strip()
    if text in {"话题列表","/topics"}:
        labels=adapter._topics.labels(chat,user)
        current=adapter._topics.current(chat,user)
        await adapter.send(chat,"当前话题："+current+"\n可切换："+"、".join(labels))
        return True
    match=re.fullmatch(r"(?:切换话题|新话题|话题|/topic)\s+(.{1,40})",text)
    if not match:
        return False
    label=match[1].strip()
    adapter._topics.select(chat,user,label)
    await adapter.send(chat,"已切换到话题「"+label+"」")
    return True

def short_chunks(text):
    """Split conversational lines, preserve structured answers/code/media and cap at three bubbles."""
    stripped=text.strip()
    if (len(stripped)>240 or any(x in text for x in ("\x60\x60\x60","MEDIA:","http://","https://","|","\n#"))
            or re.search(r"(?m)^\s*(?:[-*] |\d+[.)、])",text)):
        return [text]
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    if len(lines)==1:
        lines=[s.strip() for s in re.split(r"(?<=[。！？!?])",stripped) if s.strip()]
    if len(lines)<2 or any(len(line)>60 for line in lines):
        return [text]
    if len(lines)>3:
        return [lines[0],lines[1],"\n".join(lines[2:])]
    return lines
