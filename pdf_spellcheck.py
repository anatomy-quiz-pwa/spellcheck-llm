#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse, os, html, re
import pandas as pd
from pypdf import PdfReader

# ====== Header mapping utils ======
FULLWIDTH_PAREN_MAP = {'（':'(', '）':')'}

def normalize_header(h: str) -> str:
    if not isinstance(h, str):
        h = str(h)
    h = h.strip()
    for k,v in FULLWIDTH_PAREN_MAP.items():
        h = h.replace(k, v)
    h = re.sub(r'\s+', ' ', h)
    return h.lower()

ALIASES = {
    "en_canonical": ["en_canonical","english","en","term_en","canonical_en","標準英文","英文"],
    "zh_canonical": ["zh_canonical","chinese","zh","term_zh","canonical_zh","標準中文","中文","繁體中文"],
    "abbr": ["abbr","abbreviation","縮寫"],
    "first_mention_style": ["first_mention_style","style","首次顯示規則","first-mention"],
    "variant (錯誤用法)": ["variant (錯誤用法)","variant","variants","錯誤用法","錯字","typo","misspelling"]
}

def map_headers(df: pd.DataFrame):
    found = {normalize_header(c): c for c in df.columns}
    mapping = {}
    for required, alist in ALIASES.items():
        for a in alist:
            key = normalize_header(a)
            if key in found:
                mapping[required] = found[key]
                break
    out = pd.DataFrame()
    warnings = []
    # essential
    for col in ["en_canonical","zh_canonical","variant (錯誤用法)"]:
        if col in mapping:
            out[col] = df[mapping[col]].astype(str)
        else:
            warnings.append(f"缺少必要欄位: {col}")
            out[col] = ""
    # optional
    if "abbr" in mapping:
        out["abbr"] = df[mapping["abbr"]].astype(str)
    else:
        out["abbr"] = ""
        warnings.append("未提供 abbr，自動補空白。")
    if "first_mention_style" in mapping:
        out["first_mention_style"] = df[mapping["first_mention_style"]].astype(str)
    else:
        out["first_mention_style"] = "ZH(EN;ABBR)"
        warnings.append("未提供 first_mention_style，自動補 ZH(EN;ABBR)。")
    return out, mapping, warnings

# ====== Text utils ======
ZERO_WIDTH = "".join(["\u200b","\u200c","\u200d","\ufeff","\u00ad"])
def normalize_text(s: str) -> str:
    if not s: return ""
    s = (s.replace('（','(').replace('）',')')
           .replace('；',';').replace('，',',')
           .replace('。','.').replace('、','/'))
    for ch in ZERO_WIDTH: s = s.replace(ch,'')
    s = re.sub(r'\s+',' ',s)
    return s

def is_cjk(ch): return '\u4e00'<=ch<='\u9fff' or '\u3400'<=ch<='\u4dbf' or '\uf900'<=ch<='\ufaff'

def cjk_fuzzy_pattern(token: str) -> str:
    out=[]
    for ch in token.strip():
        if ch.isspace(): out.append(r'\s+')
        elif is_cjk(ch): out.append(re.escape(ch)+r'\s*')
        elif ch=='-': out.append(r'\s*-\s*')
        else: out.append(re.escape(ch))
    return ''.join(out)

def split_variants(cell: str):
    if not isinstance(cell,str): return []
    parts = re.split(r'[,\uFF0C;/、]+', cell.strip())
    return [p for p in parts if p]

# ====== Main ======
def load_patterns(csvfile):
    raw = pd.read_csv(csvfile)
    df, mapping, warnings = map_headers(raw)
    pats=[]
    for _,row in df.iterrows():
        for v in split_variants(str(row["variant (錯誤用法)"])):
            pats.append((re.compile(cjk_fuzzy_pattern(v),flags=re.IGNORECASE),v,row))
    return pats, warnings, mapping

def scan(pdf,pats):
    reader=PdfReader(pdf)
    hits=[]
    for pno,page in enumerate(reader.pages,start=1):
        try: raw=page.extract_text() or ""
        except: raw=""
        norm=normalize_text(raw)
        for rx,v,row in pats:
            for m in rx.finditer(norm):
                hits.append({
                    "page":pno,"variant":v,
                    "zh_canonical":row["zh_canonical"],
                    "en_canonical":row["en_canonical"],
                    "abbr":row["abbr"],
                    "first_mention_style":row["first_mention_style"],
                    "context":norm[max(0,m.start()-60):m.end()+60]
                })
    return hits

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--pdf",required=True)
    ap.add_argument("--csv",required=True)
    ap.add_argument("--out",default="report.csv")
    args=ap.parse_args()

    pats,warns,maps=load_patterns(args.csv)
    hits=scan(args.pdf,pats)
    pd.DataFrame(hits).to_csv(args.out,index=False,encoding="utf-8-sig")
    print(f"[OK] 命中 {len(hits)} 筆")
    print("欄位對應:",maps)
    if warns: print("提醒:",warns)

if __name__=="__main__": main()
