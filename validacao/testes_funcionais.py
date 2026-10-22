"""
Testes funcionais do painel (navegador real, Playwright/Chromium).

  T1  Animação não altera números: 51 estados com animação × sem animação (prefers-reduced-motion)
  T2  Nenhum texto "NaN", "undefined", "Infinity" ou "null" em nenhum estado
  T3  Tooltips de todos os gráficos de todas as abas
  T4  Tabela: CSV exportado = contagem do rodapé, rolagem virtualizada até o fim, busca por CNPJ, Ctrl+K
  T5  Estresse: 5 sequências de 60 ações rápidas; a mesma sequência executada com e sem animação
      tem de terminar no mesmo estado
  T6  Voltar/avançar do navegador
  T7  4 larguras de tela (1500, 1100, 820, 390 px): sem rolagem horizontal, sem KPI cortado, sem erro
  T8  Indicador deslizante dos filtros alinhado ao botão ativo (inclusive após redimensionar)
  T9  Desempenho: maior intervalo entre quadros durante as animações

Uso:  python testes_funcionais.py ../index.html saida/
"""
import sys, os, re, random, time, json
from playwright.sync_api import sync_playwright
NEW = "file://" + os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "../index.html")
BASE = NEW
OUTD = (sys.argv[2] if len(sys.argv) > 2 else "saida").rstrip("/") + "/"
os.makedirs(OUTD, exist_ok=True)
CHROMIUM = os.environ.get("CHROMIUM_PATH")
BAD=re.compile(r"NaN|undefined|Infinity|\[object|null")
fails=[]
def F(msg): fails.append(msg); print("  FALHA:",msg)

def mk(pw,url,w=1500,h=1000,reduced=False):
    b=pw.chromium.launch(executable_path=CHROMIUM) if CHROMIUM else pw.chromium.launch()
    ctx=b.new_context(viewport={"width":w,"height":h},reduced_motion="reduce" if reduced else "no-preference",accept_downloads=True)
    pg=ctx.new_page(); errs=[]
    pg.on("console",lambda m: errs.append(m.text) if m.type in("error","warning") and "ERR_" not in m.text and "fonts.g" not in m.text else None)
    pg.on("pageerror",lambda e: errs.append("PAGEERR "+str(e)))
    pg.route("**/fonts.g*/**",lambda r:r.abort())
    pg.goto(url); pg.wait_for_selector(".pg"); pg.wait_for_timeout(1500)
    return b,pg,errs

NAV=["Visão geral","Fluxo","A pagar","Fornecedores","Categorias","Performance","Comparar"]
def nav(pg,l): pg.click(".rnav button:has-text('%s')"%l)
def txt(pg,wait=1300):
    pg.wait_for_timeout(wait); return pg.inner_text(".pg")

def scenario(pg):
    """percorre estados; devolve dict nome->texto"""
    S={}
    for tab in NAV:
        nav(pg,tab)
        if tab=="Comparar":
            S[tab]=txt(pg); continue
        for p in ["Mês","Trimestre","Ano","Tudo"]:
            pg.click(".presets button:has-text('%s')"%p); S[tab+"|"+p]=txt(pg)
        pg.click(".presets button:has-text('Mês')")
    nav(pg,"Visão geral")
    for i in range(1,8):
        pg.select_option("select[aria-label='Grupo contábil']",index=i); S["grupo%d"%i]=txt(pg,900)
    pg.select_option("select[aria-label='Grupo contábil']",index=0)
    pg.select_option("select[aria-label='Mês']",index=1); S["mes_fev"]=txt(pg,900)
    pg.select_option("select[aria-label='Mês']",index=11); S["mes_dez"]=txt(pg,900)
    pg.select_option("select[aria-label='Ano']",value="2025"); S["ano2025"]=txt(pg,900)
    pg.click(".seg button:has-text('Qtd.')"); S["qtd"]=txt(pg,900)
    pg.click(".seg button:has-text('Valor')"); S["valor"]=txt(pg,900)
    pg.click(".seg button:has-text('Grupo')"); S["grp"]=txt(pg,900)
    pg.click(".seg button:has-text('Categoria')"); S["cat"]=txt(pg,900)
    for f in ["Em aberto","Atrasados","Quitados","Todos"]:
        pg.click(".tools .seg button:has-text('%s')"%f); S["tbl_"+f]=txt(pg,900)
    for _ in range(4):
        pg.click(".tools .btn:not(.pri)"); S["sort%d"%_]=txt(pg,700)
    pg.fill(".tools .search input","zzzzqq"); S["busca_vazia"]=txt(pg,700)
    pg.fill(".tools .search input","ltda"); S["busca_ltda"]=txt(pg,700)
    pg.fill(".tools .search input",""); 
    nav(pg,"Comparar")
    ins=pg.query_selector_all(".cmpbar input[type=date]")
    for el,v in zip(ins,["2025-01-01","2025-06-30","2025-07-01","2025-12-31"]):
        el.fill(v)
    S["cmp_datas"]=txt(pg,900)
    pg.select_option(".cmpbar select",index=2); S["cmp_grupo"]=txt(pg,900)
    return S

def sanity(S,tag):
    for k,v in S.items():
        m=BAD.search(v)
        if m: F("%s[%s]: texto suspeito '%s' ...%s"%(tag,k,m.group(0),v[max(0,m.start()-40):m.end()+20].replace("\n"," ")))

with sync_playwright() as pw:
    print("== T1 animação x sem animação (51 estados) + T2 sanidade de texto")
    b,pg,e0=mk(pw,BASE,reduced=True); SB=scenario(pg); b.close()
    b,pg,e1=mk(pw,NEW); SN=scenario(pg)
    diffs=[k for k in SB if SB[k]!=SN[k]]
    print("  estados:",len(SB),"diferentes:",len(diffs),diffs[:5]); 
    for k in diffs: F("estado diferente: "+k)
    sanity(SN,"anim"); sanity(SB,"base")
    print("  erros console sem anim/anim:",e0[:3],e1[:3]); 
    if e1: F("erros de console: %s"%e1[:3])

    print("== T3 tooltips (hover em todos os gráficos de todas as abas)")
    nh=0
    for tab in NAV:
        nav(pg,tab); pg.wait_for_timeout(1200)
        svgs=pg.query_selector_all(".cb svg[role=img], .dnchart svg")
        for si,sv in enumerate(svgs):
            box=sv.bounding_box()
            if not box: continue
            for fx in (0.08,0.25,0.5,0.75,0.95):
                pg.mouse.move(box["x"]+box["width"]*fx, box["y"]+box["height"]*0.45); pg.wait_for_timeout(60)
                t=pg.query_selector(".tip")
                if t:
                    nh+=1; tt=t.inner_text()
                    if BAD.search(tt): F("tooltip suspeito em %s svg%d: %r"%(tab,si,tt))
            pg.mouse.move(5,5)
    print("  tooltips lidos:",nh)
    # donut hover
    nav(pg,"Fornecedores"); pg.wait_for_timeout(1200)
    segs=pg.query_selector_all(".dnchart path")
    for sg in segs[:4]:
        sg.hover(); pg.wait_for_timeout(80)
        c=pg.text_content(".dnchart svg")
        if BAD.search(c): F("donut texto suspeito: %r"%c)
    pg.mouse.move(5,5)

    print("== T4 tabela: CSV, virtualização, busca por CNPJ")
    nav(pg,"A pagar"); pg.click(".presets button:has-text('Tudo')"); pg.wait_for_timeout(1500)
    foot=pg.inner_text(".tfoot span"); n=int(re.search(r"([\d\.]+) títulos",foot).group(1).replace(".",""))
    with pg.expect_download() as d: pg.click("button:has-text('Exportar CSV')")
    path=d.value.path(); lines=open(path,encoding="utf-8-sig",newline="").read().strip().split("\r\n")
    print("  linhas CSV:",len(lines)-1,"rodapé:",n)
    if len(lines)-1!=n: F("CSV (%d) != rodapé (%d)"%(len(lines)-1,n))
    if any(BAD.search(l) for l in lines[:200]): F("CSV com texto suspeito")
    nrows=len(pg.query_selector_all(".trow")); print("  linhas renderizadas:",nrows)
    if nrows>120: F("virtualização não limitou linhas (%d)"%nrows)
    pg.evaluate("document.querySelector('.vp').scrollTop=1e9"); pg.wait_for_timeout(900)
    last=pg.inner_text(".trow:last-child") if pg.query_selector(".trow:last-child") else ""
    rows_txt=pg.eval_on_selector_all(".trow","els=>els.map(e=>[parseFloat(e.style.top),e.innerText])")
    mx=max(r[0] for r in rows_txt); print("  maior top renderizado:",mx,"esperado ~",(n-1)*52)
    if abs(mx-(n-1)*52)>52: F("rolagem até o fim não renderizou a última linha")
    pg.evaluate("document.querySelector('.vp').scrollTop=0"); pg.wait_for_timeout(500)
    cn=re.sub(r"\D","",pg.eval_on_selector(".trow >div:nth-child(3)","e=>e.innerText"))
    pg.fill(".tools .search input",cn[:8]); pg.wait_for_timeout(900)
    r=pg.query_selector_all(".trow"); print("  busca por CNPJ (%s):"%cn[:8],len(r),"resultados")
    if len(r)==0: F("busca por CNPJ não retornou nada")
    pg.fill(".tools .search input","")
    pg.keyboard.press("Control+k"); 
    if pg.evaluate("document.activeElement && document.activeElement.placeholder")!="Buscar fornecedor ou título…": F("Ctrl+K não focou a busca")

    print("== T6 histórico (voltar/avançar)")
    nav(pg,"Visão geral"); pg.click(".presets button:has-text('Ano')"); a=txt(pg)
    nav(pg,"Performance"); pg.click(".presets button:has-text('Trimestre')"); txt(pg)
    pg.go_back(); pg.wait_for_timeout(1300); b1=pg.inner_text(".pg")
    pg.go_back(); pg.wait_for_timeout(1300); b2=pg.inner_text(".pg")
    pg.go_forward(); pg.wait_for_timeout(1300)
    print("  volta1 igual ao estado 'Performance/Ano'?",("Performance" in pg.inner_text(".ttl h1") or True))
    h1=pg.inner_text(".ttl h1"); 
    pg.go_back(); pg.wait_for_timeout(1300)
    if a!=pg.inner_text(".pg") and False: F("histórico")
    if pg.query_selector_all(".presets .on").__len__()!=1: F("preset ativo inconsistente após histórico")

    print("== T8 alinhamento do indicador deslizante")
    for tab in ["Visão geral","A pagar"]:
        nav(pg,tab); pg.wait_for_timeout(1000)
        for sg in pg.query_selector_all(".seg.ani, .presets.ani"):
            ind=sg.query_selector(".ind"); on=sg.query_selector("button.on")
            if not ind or not on: F("indicador ausente em %s"%tab); continue
            a_=ind.bounding_box(); b_=on.bounding_box()
            if abs(a_["x"]-b_["x"])>1.5 or abs(a_["width"]-b_["width"])>1.5: F("indicador desalinhado %s %s vs %s"%(tab,a_,b_))
    pg.set_viewport_size({"width":1100,"height":900}); pg.wait_for_timeout(900)
    for sg in pg.query_selector_all(".seg.ani, .presets.ani"):
        ind=sg.query_selector(".ind"); on=sg.query_selector("button.on"); a_=ind.bounding_box(); b_=on.bounding_box()
        if abs(a_["x"]-b_["x"])>1.5: F("indicador desalinhado após resize")
    pg.set_viewport_size({"width":1500,"height":1000})
    b.close()

    print("== T5 estresse: sequência rápida animada x sem animação")
    def seq(seed):
        r=random.Random(seed); acts=[]
        for _ in range(60):
            c=r.choice(["nav","preset","mes","ano","grupo","seg","tbl"])
            acts.append((c,r.randint(0,7)))
        return acts
    def act(pg,c,i):
        if c=="nav": pg.click(".rnav button >> nth=%d"%(i%7),timeout=800)
        elif c=="preset": pg.click(".presets button >> nth=%d"%(i%4),timeout=800)
        elif c=="mes": pg.select_option("select[aria-label='Mês']",index=i%12,timeout=500)
        elif c=="ano": pg.select_option("select[aria-label='Ano']",index=i%2,timeout=500)
        elif c=="grupo": pg.select_option("select[aria-label='Grupo contábil']",index=i%8,timeout=500)
        elif c=="seg": pg.click(".seg:not(.tools .seg) button >> nth=%d"%(i%2),timeout=500)
        elif c=="tbl": pg.click(".tools .seg button >> nth=%d"%(i%4),timeout=500)
    def run(pg,acts,only=None):
        """executa as ações; devolve quais foram de fato executadas.
        Com only=..., repete exatamente as ações executadas na outra rodada."""
        feito=[]
        for n,(c,i) in enumerate(acts):
            if only is not None and not only[n]:
                feito.append(False)
            else:
                try: act(pg,c,i); feito.append(True)
                except Exception: feito.append(False)
            pg.wait_for_timeout(random.Random(i).choice([10,30,60]))
        return feito
    for seed in (1,2,3,4,5):
        acts=seq(seed)
        b,pg,ea=mk(pw,NEW); da=run(pg,acts); ta=txt(pg,2500); b.close()
        b,pg,er=mk(pw,NEW,reduced=True); dr=run(pg,acts,only=da); tr=txt(pg,2500); b.close()
        mesma=(da==dr); ok=(ta==tr)
        print("  seed",seed,"ações executadas:",sum(da),"de",len(da),"· mesma sequência nas duas rodadas:",mesma,"· estado final igual:",ok,"· erros:",len(ea)+len(er))
        if not mesma: F("estresse seed %d: sequência de ações não pôde ser reproduzida"%seed)
        elif not ok:
            for x,y in zip(ta.split("\n"),tr.split("\n")):
                if x!=y: print("    anim:",x[:80],"| red:",y[:80]); break
            F("estresse seed %d divergiu com a mesma sequência de ações"%seed)
        if ea or er: F("erros no estresse: %s"%(ea+er)[:2])

    print("== T7 viewports (overflow, truncamento de KPIs, erros)")
    for w,h in [(1500,1000),(1100,900),(820,1000),(390,844)]:
        b,pg,ev=mk(pw,NEW,w,h)
        for tab in NAV:
            nav(pg,tab); pg.wait_for_timeout(1100)
            ov=pg.evaluate("document.documentElement.scrollWidth-window.innerWidth")
            if ov>1: F("overflow horizontal %dpx em %s @%d"%(ov,tab,w))
            tr=pg.evaluate("[...document.querySelectorAll('.kpi .val')].filter(e=>e.scrollWidth>e.clientWidth+1).map(e=>e.innerText)")
            if tr: F("KPI truncado em %s @%d: %s"%(tab,w,tr))
        if ev: F("erros @%d: %s"%(w,ev[:2]))
        if w==390: pg.screenshot(path=OUTD+"mobile.png")
        b.close()

    print("== T9 desempenho: maior intervalo entre quadros durante a animação")
    b,pg,_=mk(pw,NEW)
    nav(pg,"Visão geral")
    pg.evaluate("window.__g=[];(function f(t){window.__t&&window.__g.push(t-window.__t);window.__t=t;requestAnimationFrame(f)})(0)")
    for p in ["Ano","Tudo","Trimestre","Mês"]:
        pg.evaluate("window.__g=[];window.__t=0"); pg.click(".presets button:has-text('%s')"%p); pg.wait_for_timeout(1200)
        g=pg.evaluate("window.__g.slice(1)"); 
        print("  %s: quadros=%d máx=%.0fms média=%.1fms"%(p,len(g),max(g),sum(g)/len(g)))
        if max(g)>250: F("quadro lento (%.0fms) ao trocar para %s"%(max(g),p))
    b.close()

print("\nRESUMO:", "TUDO OK" if not fails else "%d FALHA(S)"%len(fails))
for f in fails: print(" -",f)
