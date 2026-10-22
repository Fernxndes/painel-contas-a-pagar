"""
Reconciliação do painel "Controle Financeiro" (dados fictícios).

Ideia: recalcular cada número do painel por um caminho independente
(Python/pandas lendo os dados brutos embutidos no HTML) e comparar com o
que o painel EXIBE NA TELA (lido do navegador com Playwright).

  dados brutos ──► pandas (regras de negócio escritas aqui) ──► valor esperado
       │                                                            │
       └──► painel (React, no navegador) ──► valor exato + texto na tela
                                                                    │
            (a) valor exato: |dif| ≤ R$ 0,0001 · 1e-9 relativo em % · 0 em contagens
            (b) texto: idêntico ao esperado formatado pelas mesmas regras

Uso:
    pip install pandas playwright && playwright install chromium
    python reconciliacao.py ../index.html saida/                # 61 cenários escolhidos (~2,5 min)
    python reconciliacao.py ../index.html saida_completa/ --completo   # todos os 280 recortes

Saídas:
    saida/integridade.csv       checagens sobre os dados brutos
    saida/reconciliacao.csv     uma linha por número conferido
    saida/resumo.json           totais por página e por tipo de métrica
"""
import json, re, sys, os, math, time
from datetime import date
import pandas as pd

HTML = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "../index.html")
OUT = sys.argv[2] if len(sys.argv) > 2 else "saida"
CHROMIUM = os.environ.get("CHROMIUM_PATH")          # opcional
os.makedirs(OUT, exist_ok=True)

# ════════════════════════════════════════════════════════════════════
# 1. Dados brutos
# ════════════════════════════════════════════════════════════════════
src = open(HTML, encoding="utf-8").read()
DATA = json.loads(re.search(r"<script>window\.DATA=(.*?);</script>", src).group(1))
DATAX = json.loads(re.search(r"<script>window\.DATAX=(.*?);</script>", src).group(1))
COLS = "venc baixa grupo cat forn valor pago st sit dref atraso prazo".split()
df = pd.DataFrame(DATA["rows"], columns=COLS)
df["forn_nome"] = df.forn.map(dict(enumerate(DATA["fornecedores"])))
df["grupo_nome"] = df.grupo.map(DATA["grupos"]).fillna("Outros")
df["saldo"] = (df.valor - df.pago).where(df.st == "A", 0.0)
df["ordem"] = range(len(df))                       # ordem original (desempate estável)
tit = pd.DataFrame(DATAX["titulos"], columns="doc parcela venc forn valor pago st".split())
REF = int(DATA["ref"].replace("-", ""))


def dt(ymd):
    ymd = int(ymd)
    return date(ymd // 10000, ymd // 100 % 100, ymd % 100)


def dias(a, b):
    return (dt(b) - dt(a)).days


df["dias_ref"] = [dias(REF, v) for v in df.venc]           # >0: vence depois da data-base
df["dias_atraso_ref"] = -df.dias_ref                         # >0: já vencido na data-base


def jsround(x):            # arredondamento "meio para cima", como Math.round
    return math.floor(x + 0.5)


# ════════════════════════════════════════════════════════════════════
# 2. Integridade dos dados
# ════════════════════════════════════════════════════════════════════
integ = []


def chk(nome, ok, detalhe, nivel="FALHA"):
    integ.append({"checagem": nome, "status": "OK" if ok else nivel, "detalhe": detalhe})


Q, A = df[df.st == "Q"], df[df.st == "A"]
chk("Status só assume Q (quitado) ou A (em aberto)", set(df.st) <= {"Q", "A"}, f"valores: {sorted(set(df.st))}")
chk("Nenhum valor de título ≤ 0", (df.valor <= 0).sum() == 0, f"{(df.valor <= 0).sum()} títulos")
chk("Valor pago nunca excede o valor do título", (df.pago > df.valor + 0.005).sum() == 0, f"{(df.pago > df.valor + 0.005).sum()} títulos")
chk("Títulos quitados têm pago = valor", (abs(Q.pago - Q.valor) > 0.005).sum() == 0, f"{(abs(Q.pago - Q.valor) > 0.005).sum()} divergentes")
n_parc = int((A.pago > 0).sum())
chk("Títulos em aberto sem pagamento parcial", n_parc == 0,
    f"{n_parc} títulos em aberto com pagamento parcial (R$ {A[A.pago > 0].pago.sum():.2f}); o saldo considera o abatimento", "ALERTA")
Qb = Q.dropna(subset=["baixa"])
calc = pd.Series([dias(v, b) for v, b in zip(Qb.venc, Qb.baixa)], index=Qb.index)
chk("Dias de atraso = data de baixa − vencimento (quitados)", (calc == Qb.atraso).all(),
    f"{int((calc == Qb.atraso).sum())} de {len(Qb)} conferem")
chk("Dias até a data-base = vencimento − data-base", (df.dref == df.dias_ref).all(), f"{int((df.dref == df.dias_ref).sum())} de {len(df)} conferem")
semb = Q[Q.baixa.isna()]
chk("Todo título quitado tem data de baixa", len(semb) == 0,
    f"{len(semb)} quitados sem data de baixa (R$ {semb.valor.sum():.2f}); entram como pagos e pontuais", "ALERTA")
sit_esp = pd.Series("PAGO", index=df.index).where(df.st == "Q", pd.Series("VENCIDO", index=df.index).where(df.venc < REF, "A VENCER"))
div_sit = int((sit_esp != df.sit).sum())
chk("Situação textual coerente com status e vencimento", div_sit == 0,
    f"{div_sit} títulos com situação textual divergente (o painel usa o status, não o texto)", "ALERTA")
chk("Datas de vencimento válidas e dentro da base", df.venc.between(20250101, 20261231).all(), f"{df.venc.min()} a {df.venc.max()}")
chk("Base ordenada por vencimento", df.venc.is_monotonic_increasing, "")
chk("Tabela de títulos alinhada à base (venc, fornecedor, valor, pago, status)",
    (tit.venc.values == df.venc.values).all() and (tit.forn.values == df.forn.values).all() and
    (abs(tit.valor.values - df.valor.values) < 1e-9).all() and (abs(tit.pago.values - df.pago.values) < 1e-9).all() and
    (tit.st.values == df.st.values).all(), f"{len(tit)} × {len(df)} linhas")
chk("Todo fornecedor referenciado existe no cadastro", df.forn.between(0, len(DATA["fornecedores"]) - 1).all(), f"{len(DATA['fornecedores'])} fornecedores")
chk("Nomes de fornecedor únicos", len(set(DATA["fornecedores"])) == len(DATA["fornecedores"]), "")
pj = [c for c in DATAX["fornPJ"] if c]
chk("CNPJs com 14 dígitos", all(re.fullmatch(r"\d{14}", c) for c in pj), f"{len(pj)} CNPJs")
chk("Prazo médio (emissão → baixa) auditável", False,
    "a base não traz a data de emissão; o prazo é conferido só quanto ao cálculo da média", "LIMITAÇÃO")
pd.DataFrame(integ).to_csv(f"{OUT}/integridade.csv", index=False, encoding="utf-8-sig")


# ════════════════════════════════════════════════════════════════════
# 3. Regras de negócio (implementação independente)
# ════════════════════════════════════════════════════════════════════
MES = "jan fev mar abr mai jun jul ago set out nov dez".split()


def last_day(y, m):
    return (date(y + (m == 12), m % 12 + 1, 1) - date(y, m, 1)).days


def janela(preset, ano, mes):
    if preset == "tudo":
        return 20250101, 20261231
    if preset == "ano":
        return ano * 10000 + 101, ano * 10000 + 1231
    if preset == "tri":
        m1 = (mes - 1) // 3 * 3 + 1
        return ano * 10000 + m1 * 100 + 1, ano * 10000 + (m1 + 2) * 100 + last_day(ano, m1 + 2)
    return ano * 10000 + mes * 100 + 1, ano * 10000 + mes * 100 + last_day(ano, mes)


def anterior(preset, ano, mes):
    if preset == "tudo":
        return None
    if preset == "ano":
        return janela("ano", ano - 1, 1)
    if preset == "tri":
        m = (mes - 1) // 3 * 3 + 1 - 3
        return janela("tri", ano - (m < 1), m + 12 if m < 1 else m)
    return janela("mes", ano - (mes == 1), 12 if mes == 1 else mes - 1)


def recorte(lo, hi, grupo=""):
    s = df[(df.venc >= lo) & (df.venc <= hi)]
    return s[s.grupo == grupo] if grupo else s


def resumo(s):
    q, a = s[s.st == "Q"], s[s.st == "A"]
    venc, avenc = a[a.venc < REF], a[a.venc >= REF]
    ok = q[q.atraso <= 0]
    late = q[q.atraso > 0]
    return dict(
        t=s.valor.sum(), pg=s.pago.sum(), n=len(s),
        ab=a.saldo.sum(), nA=len(a), at=venc.saldo.sum(), nAt=len(venc),
        v7=avenc[avenc.dias_ref <= 7].saldo.sum(), n7=int((avenc.dias_ref <= 7).sum()),
        v30=avenc[avenc.dias_ref <= 30].saldo.sum(), n30=int((avenc.dias_ref <= 30).sum()),
        nQ=len(q), ok=len(ok), tarde=len(q) - len(ok),
        pont=len(ok) / len(q) * 100 if len(q) else None,   # sem nenhum pagamento a pontualidade não existe (painel mostra "—")
        med=jsround(late.atraso.mean()) if len(late) else 0,
        prazo=jsround(s.prazo.mean()) if len(s) else 0,
        pg_quit=q.pago.sum(),
    )


def variacao(c, p):
    return None if (c is None or p is None or p == 0) else (c - p) / abs(p) * 100


def buckets(preset, ano, mes, lo, hi):
    """rótulos e função ymd -> índice da janela (-1 fora do período)."""
    if preset == "mes":
        ld = last_day(ano, mes)
        fx = [(1, 7), (8, 14), (15, 21), (22, 28)] + ([(29, ld)] if ld >= 29 else [])
        labels = [f"{a:02d}–{b:02d}" for a, b in fx]
        def key(ymd):
            ymd = int(ymd)
            if ymd // 100 != ano * 100 + mes:
                return -1
            d = ymd % 100
            return next(i for i, (a, b) in enumerate(fx) if a <= d <= b)
        return labels, key
    y, m = lo // 10000, lo // 100 % 100
    idx, labels = {}, []
    while (y, m) <= (hi // 10000, hi // 100 % 100):
        idx[y * 100 + m] = len(labels)
        labels.append(f"{MES[m - 1]}/{str(y)[2:]}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return labels, lambda ymd: idx.get(int(ymd) // 100, -1)


# ════════════════════════════════════════════════════════════════════
# 4. Leitura do painel + comparação com tolerância praticamente nula
# ════════════════════════════════════════════════════════════════════
# Cada número é conferido duas vezes:
#   (a) VALOR EXATO: o painel expõe, em um atributo invisível data-v, o número
#       sem arredondamento e sem animação. Ele tem de bater com o recalculado:
#         reais ........ |diferença| ≤ R$ 0,0001   (um centésimo de centavo)
#         percentuais .. |diferença| ≤ 1e-9 relativo (um bilionésimo)
#         contagens .... diferença = 0
#   (b) TEXTO NA TELA: o texto exibido tem de ser IDÊNTICO, caractere por
#       caractere, ao valor esperado formatado com as mesmas regras de
#       arredondamento do painel (meio para cima, padrão pt-BR).
from decimal import Decimal, ROUND_HALF_UP

TOL_REAIS, TOL_REL = 1e-4, 1e-9


def norm(s):
    return (s or "").replace("\xa0", " ").replace(" ", " ").strip()


def fixed(x, n):
    """equivalente a Number.prototype.toFixed(n): arredonda o valor binário exato, meio para cima."""
    return Decimal(x).quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP)


def grp(n):
    return f"{int(n):,}".replace(",", ".")


def jsround(x):            # Math.round
    return math.floor(x + 0.5)


def f_brl0(v):
    v = round(v, 2)                       # valores em reais: centavos exatos antes de formatar
    return ("-" if v < 0 else "") + "R$ " + grp(fixed(abs(v), 0))


def f_brl2(v):
    v = round(v, 2)
    q = fixed(abs(v), 2)
    i, d = str(q).split(".")
    return ("-" if v < 0 else "") + "R$ " + grp(i) + "," + d


def f_comp(v):
    v = round(v, 2)
    a, s = abs(v), "-" if v < 0 else ""
    if a >= 1e9:
        return s + "R$ " + str(fixed(a / 1e9, 2)).replace(".", ",") + " bi"
    if a >= 1e6:
        return s + "R$ " + str(fixed(a / 1e6, 2)).replace(".", ",") + " mi"
    if a >= 1e3:
        return s + "R$ " + str(fixed(a / 1e3, 0)) + " mil"
    return f_brl0(v)


def f_pct(v):
    return str(fixed(v, 1)).replace(".", ",") + "%"


def f_int(v):
    return grp(jsround(v))


def f_dias(v):
    n = jsround(v)
    return f"{n} dia" if n == 1 else f"{n} dias"


def f_ax(v):
    v = round(v, 2)
    a = abs(v)
    if a >= 1e6:
        return str(fixed(v / 1e6, 1)).replace(".", ",") + "M"
    if a >= 1e3:
        return str(jsround(v / 1e3)) + "k"
    return str(jsround(v))


FMT = {"brl2": f_brl2, "comp": f_comp, "brl0": f_brl0, "pct": f_pct, "int": f_int, "dias": f_dias, "ax": f_ax,
       "dias_exato": f_dias,                       # média exata, exibida arredondada em dias
       "pct_sinal": lambda v: f_pct(abs(v))}       # variação: tela mostra |%|, valor exato tem sinal


def tipo(txt):
    s = norm(txt)
    if re.fullmatch(r"-?R\$ [\d\.]+,\d\d", s): return "brl2"
    if re.fullmatch(r"-?R\$ [\d,]+ (bi|mi|mil)", s): return "comp"
    if re.fullmatch(r"-?R\$ [\d\.]+", s): return "brl0"
    if re.fullmatch(r"[\d\.]*\d,\d%", s): return "pct"
    if re.fullmatch(r"\d+ dias?", s): return "dias"
    if re.fullmatch(r"[\d,]+M|\d+k", s): return "ax"
    if re.fullmatch(r"[\d\.]+", s): return "int"
    raise ValueError(f"formato não reconhecido: {txt!r}")


rows = []


def cmp(cen, pagina, metrica, esperado, exibido, exato=None, nota="", kind=None):
    """(a) valor exato vs esperado  +  (b) texto exibido == esperado formatado."""
    txt = norm(exibido)
    rec = dict(cenario=cen, pagina=pagina, metrica=metrica, esperado=esperado, valor_exato_painel=None, diferenca=None,
               tolerancia=None, exibido=txt, esperado_formatado=None, texto_identico=None, status="OK", nota=nota)
    try:
        k = kind or tipo(txt)
        rec["esperado_formatado"] = FMT[k](esperado)
        rec["texto_identico"] = rec["esperado_formatado"] == txt
        if exato is None:
            if k in ("int", "dias"):         # inteiros: o texto já é o valor exato
                exato = float(txt.split()[0].replace(".", ""))
            else:
                raise ValueError("número com casas decimais sem valor exato exposto (data-v)")
        x = float(exato)
        tol = 0.0 if k in ("int", "dias") else (TOL_REL * max(1.0, abs(esperado)) if k in ("pct", "pct_sinal", "dias_exato") else TOL_REAIS)
        rec.update(valor_exato_painel=x, diferenca=x - esperado, tolerancia=tol)
        if abs(x - esperado) > tol or not rec["texto_identico"]:
            rec["status"] = "DIVERGE"
    except Exception as e:
        rec.update(status="ERRO", nota=f"{nota} {e}".strip())
    rows.append(rec)


def cmp_pont(cen, pagina, metrica, v, exibido, exato=None):
    """pontualidade: período sem pagamentos tem de aparecer como "—", nunca como 0,0%."""
    if v is None:
        cmp_txt(cen, pagina, metrica, "—", exibido, "sem pagamentos no período: pontualidade não se aplica")
    else:
        cmp(cen, pagina, metrica, v, exibido, exato)


def cmp_txt(cen, pagina, metrica, esperado, exibido, nota=""):
    ok = norm(esperado) == norm(exibido)
    rows.append(dict(cenario=cen, pagina=pagina, metrica=metrica, esperado=norm(esperado), valor_exato_painel=None, diferenca=None,
                     tolerancia=0, exibido=norm(exibido), esperado_formatado=norm(esperado), texto_identico=ok,
                     status="OK" if ok else "DIVERGE", nota=nota))


def cmp_delta(cen, pagina, metrica, d, kp, inverso=False):
    """variação vs período anterior: sinal e valor exatos, texto e cor (bom/ruim)."""
    t, x, cls = norm(kp["delta"]["t"]).replace("—", "").strip(), kp["delta"]["x"], kp["delta"]["cls"] or ""
    if d is None:
        cmp_txt(cen, pagina, metrica + " · variação", "sem comparação", t)
        return
    rec = dict(cenario=cen, pagina=pagina, metrica=metrica + " · variação (com sinal)", esperado=d, valor_exato_painel=None,
               diferenca=None, tolerancia=TOL_REL * max(1.0, abs(d)), exibido=t, esperado_formatado=None, texto_identico=None,
               status="OK", nota="")
    try:
        xv = float(x)
        esp_txt = ">999%" if abs(d) > 999 else f_pct(abs(d))
        rec.update(valor_exato_painel=xv, diferenca=xv - d, esperado_formatado=esp_txt, texto_identico=(esp_txt == t))
        if abs(xv - d) > rec["tolerancia"] or esp_txt != t:
            rec["status"] = "DIVERGE"
    except Exception as e:
        rec.update(status="ERRO", nota=str(e))
    rows.append(rec)
    up, dn = d > 0.05, d < -0.05
    good = dn if inverso else up
    esp = "fl" if not (up or dn) else ("up" if good else "dn")
    cmp_txt(cen, pagina, metrica + " · cor da variação", esp, cls.replace("delta", "").strip(),
            "verde = bom, vermelho = ruim" + (" (indicador inverso)" if inverso else ""))


def curto(t, n):
    return t[:n - 1] + "…" if t and len(t) > n else t


def first_num(s, pat=r"([\d\.]+)"):
    return re.search(pat, norm(s)).group(1)


def hint_x(k, i=0):
    return json.loads(k["hint"]["x"])[i] if k["hint"]["x"] else None


SNAP_JS = r"""() => {
  const q=(e,s)=>e?e.querySelector(s):null, T=e=>e?(e.innerText!==undefined?e.innerText:e.textContent).trim():null;
  const X=e=>{ if(!e) return null; const d=e.hasAttribute('data-v')?e:e.querySelector('[data-v]'); return d?d.getAttribute('data-v'):null; };
  const V=e=>({t:T(e),x:X(e)});
  const kpis=[...document.querySelectorAll('.pgx .kpi')].map(k=>{const dl=q(k,'.delta'), h=q(k,'.hint');
     return {lab:T(q(k,'.lab')),val:V(q(k,'.val')),
       delta:{t:T(dl),x:dl?dl.getAttribute('data-v'):null,cls:dl?dl.className:null},
       hint:{t:T(h),x:h?h.getAttribute('data-v'):null}};});
  const cards=[...document.querySelectorAll('.pgx .card')].map(c=>({
    title:T(q(c,'h3')),sub:T(q(c,'.cs')),
    leg:[...c.querySelectorAll('.leg > span')].map(V),
    crow:[...c.querySelectorAll('.crow')].map(r=>({nm:q(r,'.nm').title,vl:V(q(r,'.vl')),pc:V(q(r,'.pc'))})),
    ag:[...c.querySelectorAll('.agrow')].map(r=>({lb:T(q(r,'.lb')),vv:V(q(r,'.vv > [data-v]')),sm:V(q(r,'.vv small [data-v]'))})),
    rk:[...c.querySelectorAll('.rkrow')].map(r=>({nm:q(r,'.nm').title,vv:V(q(r,'.vv > [data-v]')),sm:T(q(r,'.vv small'))})),
    dn:[...c.querySelectorAll('.dnli')].map(r=>({nm:q(r,'.nm').title,pc:V(q(r,'.pc'))})),
    dnc:V(q(c,'.dnchart svg [data-v]')),
    big:V(q(c,'.mhead .big')),
    tiles:[...c.querySelectorAll('.tile')].map(t=>({k:T(q(t,'.k')),v:V(q(t,'.v [data-v]'))})),
    stk:[...c.querySelectorAll('.srow')].map(r=>({nm:q(r,'.nm').title,tt:V(q(r,'.tt'))})),
    heat:q(c,'.heat table')?{cols:[...c.querySelectorAll('thead th')].slice(1).map(T),
       rows:[...c.querySelectorAll('tbody tr')].map(tr=>({lb:q(tr,'td.lb').title,cells:[...tr.querySelectorAll('td.cl')].map(V)}))}:null,
    vs:[...c.querySelectorAll('.vsrow')].map(r=>[...r.children].map(V)),
    tfoot:q(c,'.tfoot')?{t:T(q(c,'.tfoot')),xs:[...c.querySelectorAll('.tfoot [data-v]')].map(e=>({t:T(e),x:e.getAttribute('data-v')}))}:null
  }));
  return {kpis,cards,h1:T(document.querySelector('.ttl h1'))};
}"""


def kpi(snap, lab):
    return next(k for k in snap["kpis"] if k["lab"] == lab)


def card(snap, title_start):
    return next(c for c in snap["cards"] if (c["title"] or "").startswith(title_start))


def legv(item, rotulo):
    """valor da legenda 'Rótulo R$ x': texto sem o rótulo + valor exato."""
    return item["t"].split("\n")[-1].replace(rotulo, "").strip(), item["x"]


# ─────────────────────────── conferências por página ───────────────────────────
def conf_hub(cen, snap, k, kp):
    P = "Visão geral"
    dl = lambda f: variacao(k[f], kp[f] if kp else None)
    K = kpi(snap, "Total do período")
    cmp(cen, P, "Total do período", k["t"], K["val"]["t"], K["val"]["x"]); cmp_delta(cen, P, "Total do período", dl("t"), K)
    cmp(cen, P, "Total · nº de títulos", k["n"], first_num(K["hint"]["t"]))
    if k["t"]:
        cmp(cen, P, "Total · % já pago", k["pg"] / k["t"] * 100, first_num(K["hint"]["t"], r"·\s*([\d,]+%)"), hint_x(K))
    for lab, f, inv, cnt in [("Pago", "pg", False, "nQ"), ("Em aberto", "ab", True, "nA"), ("Vencido", "at", True, "nAt")]:
        K = kpi(snap, lab)
        cmp(cen, P, lab, k[f], K["val"]["t"], K["val"]["x"]); cmp_delta(cen, P, lab, dl(f), K, inv)
        cmp(cen, P, lab + " · nº de títulos", k[cnt], first_num(K["hint"]["t"]))
    for lab, f, c in [("A vencer em 7 dias", "v7", "n7"), ("A vencer em 30 dias", "v30", "n30")]:
        K = kpi(snap, lab)
        cmp(cen, P, lab, k[f], K["val"]["t"], K["val"]["x"]); cmp(cen, P, lab + " · nº de títulos", k[c], first_num(K["hint"]["t"]))
    K = kpi(snap, "Prazo médio de pagamento")
    cmp(cen, P, "Prazo médio de pagamento", k["prazo"], K["val"]["t"], K["val"]["x"])
    g = SUB.groupby("grupo_nome").valor.sum().sort_values(ascending=False)
    C = card(snap, "Composição")
    shown = {r["nm"]: r for r in C["crow"]}
    for nome, v in g.items():
        if nome in shown:
            cmp(cen, P, f"Composição · {nome} · valor", v, shown[nome]["vl"]["t"], shown[nome]["vl"]["x"])
            cmp(cen, P, f"Composição · {nome} · % do total", v / k["t"] * 100, shown[nome]["pc"]["t"], shown[nome]["pc"]["x"])
        else:
            cmp_txt(cen, P, f"Composição · {nome}", "exibido", "(não exibido)", "grupo com valor no período não aparece na lista")
    C = card(snap, "Curva de desembolso")
    t, x = legv(C["leg"][0], "Vence"); cmp(cen, P, "Curva · total que vence", k["t"], t, x)
    t, x = legv(C["leg"][1], "Pago"); cmp(cen, P, "Curva · total pago (quitados)", k["pg_quit"], t, x)
    C = card(snap, "Pontualidade")
    cmp_pont(cen, P, "Pontualidade", k["pont"], C["big"]["t"], C["big"]["x"])
    tiles = {t["k"]: t["v"] for t in C["tiles"]}
    cmp(cen, P, "Pontualidade · no prazo", k["ok"], tiles["No prazo"]["t"], tiles["No prazo"]["x"])
    cmp(cen, P, "Pontualidade · com atraso", k["tarde"], tiles["Com atraso"]["t"], tiles["Com atraso"]["x"])
    cmp(cen, P, "Pontualidade · atraso médio (dias)", k["med"], tiles["Atraso médio"]["t"], tiles["Atraso médio"]["x"])


def conf_tabela(cen, snap, k, filtro):
    P = "Tabela de títulos"
    tf = card(snap, "Títulos do período")["tfoot"]
    esp_n = {"Todos": k["n"], "Em aberto": k["nA"], "Atrasados": k["nAt"], "Quitados": k["nQ"]}[filtro]
    if tf is None:   # tabela vazia: mostra a mensagem de "nenhum título"
        cmp(cen, P, f"Filtro {filtro} · nº de títulos (tabela vazia)", esp_n, "0")
        return
    rotulo = re.search(r"(Total [^:]+):", norm(tf["t"])).group(1)
    cnt, tot = tf["xs"][0], tf["xs"][1]
    cmp(cen, P, f"Filtro {filtro} · nº de títulos", esp_n, cnt["t"], cnt["x"])
    if rotulo == "Total pago":
        esperado = SUB[SUB.st == "Q"].pago.sum()
    else:  # o rótulo diz "em aberto": o valor tem de ser saldo em aberto
        sub = {"Todos": SUB, "Em aberto": SUB[SUB.st == "A"], "Atrasados": SUB[(SUB.st == "A") & (SUB.venc < REF)],
               "Quitados": SUB[SUB.st == "Q"]}[filtro]
        esperado = sub.saldo.sum()
    cmp(cen, P, f"Filtro {filtro} · '{rotulo}'", esperado, tot["t"], tot["x"], "valor tem de corresponder ao rótulo exibido")


def conf_fluxo(cen, snap, k, kp):
    P = "Fluxo"
    dl = lambda f: variacao(k[f], kp[f] if kp else None)
    for lab, f, inv in [("Saídas previstas", "t", False), ("Saídas realizadas", "pg", False), ("A desembolsar", "ab", True)]:
        K = kpi(snap, lab); cmp(cen, P, lab, k[f], K["val"]["t"], K["val"]["x"]); cmp_delta(cen, P, lab, dl(f), K, inv)
    if k["t"]:
        K = kpi(snap, "Saídas realizadas")
        cmp(cen, P, "Saídas realizadas · % executado", k["pg"] / k["t"] * 100, first_num(K["hint"]["t"], r"([\d,]+%)"), hint_x(K))
    K = kpi(snap, "Necessidade em 7 dias")
    cmp(cen, P, "Necessidade em 7 dias", k["v7"], K["val"]["t"], K["val"]["x"])
    cmp(cen, P, "Necessidade em 7 dias · nº", k["n7"], first_num(K["hint"]["t"]))
    C = card(snap, "Desembolso acumulado")
    t, x = legv(C["leg"][1], "A desembolsar (acumulado)"); cmp(cen, P, "Desembolso acumulado · total", k["ab"], t, x)
    C = card(snap, "Realizado × a realizar")
    parts = {"Já pago": k["pg"], "Em aberto (a vencer)": max(0, k["ab"] - k["at"]), "Em aberto (vencido)": k["at"]}
    tot = sum(parts.values()) or 1
    sh = {r["nm"]: r for r in C["crow"]}
    for nome, v in parts.items():
        cmp(cen, P, f"Realizado × a realizar · {nome}", v, sh[nome]["vl"]["t"], sh[nome]["vl"]["x"])
        cmp(cen, P, f"Realizado × a realizar · {nome} %", v / tot * 100, sh[nome]["pc"]["t"], sh[nome]["pc"]["x"])


def conf_cap(cen, snap, k, kp):
    P = "A pagar"
    dl = lambda f: variacao(k[f], kp[f] if kp else None)
    for lab, f, inv in [("Em aberto (saldo)", "ab", True), ("Vencido em aberto", "at", True), ("Títulos no período", "n", False)]:
        K = kpi(snap, lab); cmp(cen, P, lab, k[f], K["val"]["t"], K["val"]["x"]); cmp_delta(cen, P, lab, dl(f), K, inv)
    K = kpi(snap, "Prazo médio de pagamento"); cmp(cen, P, "Prazo médio de pagamento", k["prazo"], K["val"]["t"], K["val"]["x"])
    a = SUB[SUB.st == "A"]
    bands = [("Vencido há mais de 7 dias", a[(a.venc < REF) & (a.dias_atraso_ref > 7)]),
             ("Vencido há até 7 dias", a[(a.venc < REF) & (a.dias_atraso_ref <= 7)]),
             ("Vence em até 7 dias", a[(a.venc >= REF) & (a.dias_ref <= 7)]),
             ("Vence em 8–15 dias", a[(a.venc >= REF) & a.dias_ref.between(8, 15)]),
             ("Vence em mais de 15 dias", a[(a.venc >= REF) & (a.dias_ref > 15)])]
    soma = sum(b.saldo.sum() for _, b in bands)
    rows.append(dict(cenario=cen, pagina=P, metrica="Faixas · soma das faixas = saldo em aberto", esperado=k["ab"], valor_exato_painel=None,
                     diferenca=soma - k["ab"], tolerancia=TOL_REAIS, exibido="", esperado_formatado="", texto_identico=None,
                     status="OK" if abs(soma - k["ab"]) <= TOL_REAIS else "DIVERGE", nota="consistência interna das regras"))
    tot = soma or 1
    C = card(snap, "Faixas de vencimento")
    sh = {r["lb"]: r for r in C["ag"]}
    for nome, b in bands:
        cmp(cen, P, f"Faixa {nome}", b.saldo.sum(), sh[nome]["vv"]["t"], sh[nome]["vv"]["x"])
        cmp(cen, P, f"Faixa {nome} · %", b.saldo.sum() / tot * 100, sh[nome]["sm"]["t"], sh[nome]["sm"]["x"])
    crit = a.assign(ordv=(a.venc >= REF).astype(int)).sort_values(["ordv", "saldo", "ordem"], ascending=[True, False, True], kind="mergesort").head(12)
    C = card(snap, "Títulos críticos")
    for i, (r, s) in enumerate(zip(crit.itertuples(), C["rk"])):
        cmp_txt(cen, P, f"Críticos #{i + 1} · fornecedor", r.forn_nome, s["nm"])
        cmp(cen, P, f"Críticos #{i + 1} · saldo", r.saldo, s["vv"]["t"], s["vv"]["x"])
        extra = f"{dias(r.venc, REF)}d de atraso" if r.venc < REF else f"vence {dt(r.venc).strftime('%d/%m/%Y')}"
        cmp_txt(cen, P, f"Críticos #{i + 1} · situação", extra, s["sm"])


def conf_forn(cen, snap, k, kp):
    P = "Fornecedores"
    f = SUB.groupby("forn_nome").agg(v=("valor", "sum"), n=("valor", "size"))
    f = f.sort_values(["v"], ascending=False, kind="mergesort")
    tot = f.v.sum()
    K = kpi(snap, "Fornecedores ativos"); cmp(cen, P, "Fornecedores ativos", len(f), K["val"]["t"], K["val"]["x"])
    K = kpi(snap, "Concentração top 10"); cmp(cen, P, "Concentração top 10", f.v.head(10).sum() / tot * 100 if tot else 0.0, K["val"]["t"], K["val"]["x"])
    K = kpi(snap, "Maior fornecedor")
    cmp(cen, P, "Maior fornecedor · valor", f.v.iloc[0] if len(f) else 0.0, K["val"]["t"], K["val"]["x"])
    cmp_txt(cen, P, "Maior fornecedor · nome", curto(f.index[0], 28) if len(f) else "—", K["hint"]["t"])
    K = kpi(snap, "Em aberto"); cmp(cen, P, "Em aberto", k["ab"], K["val"]["t"], K["val"]["x"])
    cmp_delta(cen, P, "Em aberto", variacao(k["ab"], kp["ab"] if kp else None), K, True)
    C = card(snap, "Ranking de fornecedores")
    for i, ((nome, r), s) in enumerate(zip(f.head(12).iterrows(), C["rk"])):
        cmp_txt(cen, P, f"Ranking #{i + 1} · nome", nome, s["nm"])
        cmp(cen, P, f"Ranking #{i + 1} · valor", r.v, s["vv"]["t"], s["vv"]["x"]); cmp(cen, P, f"Ranking #{i + 1} · títulos", r.n, first_num(s["sm"]))
    C = card(snap, "Participação dos maiores")
    cmp(cen, P, "Rosca · total no centro", tot, C["dnc"]["t"], C["dnc"]["x"])
    for i, (nome, r) in enumerate(f.head(8).iterrows()):
        s = C["dn"][i]; cmp_txt(cen, P, f"Rosca #{i + 1} · nome", nome, s["nm"]); cmp(cen, P, f"Rosca #{i + 1} · %", r.v / tot * 100, s["pc"]["t"], s["pc"]["x"])
    if len(f) > 8:
        cmp(cen, P, "Rosca · demais %", f.v.iloc[8:].sum() / tot * 100, C["dn"][8]["pc"]["t"], C["dn"][8]["pc"]["x"])
    acum = (f.v.head(20).cumsum() / tot * 100).tolist() if tot else []
    if not len(f):
        esp = "Nenhum fornecedor com títulos no período"
    elif acum and acum[-1] >= 80:
        nv = next(i + 1 for i, c in enumerate(acum) if c >= 80)
        esp = f"{nv} fornecedores concentram 80% do valor do período (em destaque)"
    else:
        esp = "Os 20 maiores não chegam a 80% do valor — gasto pulverizado"
    cmp_txt(cen, P, "Pareto · conclusão do subtítulo", esp, card(snap, "Curva de Pareto")["sub"])


def conf_desp(cen, snap, k, kp, labels, key):
    P = "Categorias"
    s = SUB
    c = s.groupby("cat").agg(t=("valor", "sum"), n=("valor", "size")).sort_values("t", ascending=False, kind="mergesort")
    tot = c.t.sum()
    for (nome, r), K in zip(c.head(4).iterrows(), snap["kpis"]):
        cmp_txt(cen, P, "KPI categoria · nome", nome, K["lab"]); cmp(cen, P, f"KPI {nome} · valor", r.t, K["val"]["t"], K["val"]["x"])
        cmp(cen, P, f"KPI {nome} · % do total", r.t / tot * 100, first_num(K["hint"]["t"], r"([\d,]+%)"), hint_x(K))
        cmp(cen, P, f"KPI {nome} · títulos", r.n, first_num(K["hint"]["t"], r"·\s*([\d\.]+)"))
    C = card(snap, "Participação por categoria")
    sh = {x["nm"]: x for x in C["crow"]}
    for nome, r in c.head(8).iterrows():
        cmp(cen, P, f"Participação · {nome} · % do total", r.t / tot * 100, sh[nome]["pc"]["t"], sh[nome]["pc"]["x"])
    C = card(snap, "Situação por categoria")
    sh = {x["nm"]: x for x in C["stk"]}
    for nome in c.head(8).index:
        x = s[s.cat == nome]
        v = x[x.st == "Q"].pago.sum() + x.saldo.sum()
        cmp(cen, P, f"Situação · {nome} · total", v, sh[nome]["tt"]["t"], sh[nome]["tt"]["x"])
    C = card(snap, "Concentração no tempo")
    cmp_txt(cen, P, "Mapa de calor · colunas", " | ".join(labels), " | ".join(C["heat"]["cols"]))
    for nome, hr in zip(c.head(8).index, C["heat"]["rows"]):
        cmp_txt(cen, P, "Mapa de calor · linha", nome, hr["lb"])
        x = s[s.cat == nome]
        kk = x.venc.map(key)
        for j, cell in enumerate(hr["cells"]):
            v = x[kk == j].valor.sum()
            if cell["t"] == "—":     # sem valor na janela
                xv = float(cell["x"]) if cell["x"] is not None else None
                ok = xv is not None and abs(xv - v) <= TOL_REAIS and v <= 0.5
                rows.append(dict(cenario=cen, pagina=P, metrica=f"Mapa de calor · {nome} · {labels[j]}", esperado=v, valor_exato_painel=xv,
                                 diferenca=None if xv is None else xv - v, tolerancia=TOL_REAIS, exibido="—", esperado_formatado="—",
                                 texto_identico=v <= 0.5, status="OK" if ok else "DIVERGE", nota=""))
            else:
                cmp(cen, P, f"Mapa de calor · {nome} · {labels[j]}", v, cell["t"], cell["x"], kind="ax")


def conf_perf(cen, snap, k, kp, labels, key):
    P = "Performance"
    dl = lambda f: variacao(k[f], kp[f] if kp else None)
    K = kpi(snap, "Pontualidade"); cmp_pont(cen, P, "Pontualidade", k["pont"], K["val"]["t"], K["val"]["x"]); cmp_delta(cen, P, "Pontualidade", dl("pont"), K)
    cmp_txt(cen, P, "Pontualidade · dica", f"{f_int(k['ok'])} pagos no prazo" if k["nQ"] else "sem pagamentos no período", K["hint"]["t"])
    K = kpi(snap, "Pagamentos no período"); cmp(cen, P, "Pagamentos no período", k["nQ"], K["val"]["t"], K["val"]["x"]); cmp_delta(cen, P, "Pagamentos no período", dl("nQ"), K)
    K = kpi(snap, "Pagos em atraso"); cmp(cen, P, "Pagos em atraso", k["tarde"], K["val"]["t"], K["val"]["x"]); cmp_delta(cen, P, "Pagos em atraso", dl("tarde"), K, True)
    if k["nQ"]:
        cmp(cen, P, "Pagos em atraso · % do total pago", k["tarde"] / k["nQ"] * 100, first_num(K["hint"]["t"], r"([\d,]+%)"), hint_x(K))
    K = kpi(snap, "Vencidos em aberto"); cmp(cen, P, "Vencidos em aberto", k["nAt"], K["val"]["t"], K["val"]["x"])
    K = kpi(snap, "Atraso médio"); cmp(cen, P, "Atraso médio", k["med"], K["val"]["t"], K["val"]["x"])
    q = SUB[SUB.st == "Q"]
    when = q.baixa.fillna(q.venc).astype(int)
    kk = when.map(key)
    C = card(snap, "No prazo × em atraso")
    tot_tela = 0
    for j, (lab, s) in enumerate(zip(labels, C["stk"])):
        cmp(cen, P, f"Liquidações · {lab}", int((kk == j).sum()), first_num(s["tt"]["t"]), s["tt"]["x"], "pagamentos cuja baixa cai na janela")
        tot_tela += float(s["tt"]["x"])
    cmp(cen, P, "Liquidações · total nas janelas", int((kk >= 0).sum()), f_int(tot_tela), tot_tela)
    late = q[q.atraso > 0]
    g = late.groupby("forn_nome").agg(v=("atraso", "mean"), n=("atraso", "size"))
    g["vol"] = SUB.groupby("forn_nome").valor.sum().reindex(g.index)   # desempate: maior volume no período
    g = g[g.n >= 3].sort_values(["v", "vol"], ascending=False, kind="mergesort").head(8)
    C = card(snap, "Maiores atrasos médios")
    for i, ((nome, r), s) in enumerate(zip(g.iterrows(), C["rk"])):
        cmp_txt(cen, P, f"Maiores atrasos #{i + 1} · fornecedor", nome, s["nm"])
        cmp(cen, P, f"Maiores atrasos #{i + 1} · média (dias)", r.v, s["vv"]["t"], s["vv"]["x"], kind="dias_exato")
        cmp(cen, P, f"Maiores atrasos #{i + 1} · pagamentos atrasados", r.n, first_num(s["sm"]))
    C = card(snap, "Pontualidade")
    cmp_pont(cen, "Performance · Pontualidade", "Medidor", k["pont"], C["big"]["t"], C["big"]["x"])


def conf_cmp(cen, snap, A_, B_, grupo):
    P = "Comparar"
    sa, sb = recorte(*A_, grupo), recorte(*B_, grupo)
    ka, kb = resumo(sa), resumo(sb)
    K = kpi(snap, "Total — A"); cmp(cen, P, "Total A", ka["t"], K["val"]["t"], K["val"]["x"]); cmp(cen, P, "Total A · títulos", ka["n"], first_num(K["hint"]["t"]))
    K = kpi(snap, "Total — B"); cmp(cen, P, "Total B", kb["t"], K["val"]["t"], K["val"]["x"]); cmp(cen, P, "Total B · títulos", kb["n"], first_num(K["hint"]["t"]))
    cmp_delta(cen, P, "Total B vs A", variacao(kb["t"], ka["t"]), K)
    K = kpi(snap, "Em aberto — B"); cmp(cen, P, "Em aberto B", kb["ab"], K["val"]["t"], K["val"]["x"])
    cmp(cen, P, "Em aberto A (dica)", ka["ab"], K["hint"]["t"].replace("A:", "").strip(), hint_x(K))
    cmp_delta(cen, P, "Em aberto B vs A", variacao(kb["ab"], ka["ab"]), K, True)
    K = kpi(snap, "Pontualidade — B"); cmp_pont(cen, P, "Pontualidade B", kb["pont"], K["val"]["t"], K["val"]["x"])
    cmp_pont(cen, P, "Pontualidade A (dica)", ka["pont"], K["hint"]["t"].replace("A:", "").strip(), hint_x(K) if ka["pont"] is not None else None)
    ca, cb = sa.groupby("cat").valor.sum(), sb.groupby("cat").valor.sum()
    cats = pd.DataFrame({"a": ca, "b": cb}).fillna(0)
    cats["d"] = cats.b - cats.a
    cats = cats.reindex(cats.d.abs().sort_values(ascending=False, kind="mergesort").index).head(12)
    C = card(snap, "Variação por categoria")
    sh = {r[0]["t"]: r for r in C["vs"]}
    for nome, r in cats.iterrows():
        s = sh.get(nome)
        if s is None:
            cmp_txt(cen, P, f"Variação · {nome}", "presente", "ausente"); continue
        cmp(cen, P, f"Variação · {nome} · A", r.a, s[1]["t"], s[1]["x"]); cmp(cen, P, f"Variação · {nome} · B", r.b, s[2]["t"], s[2]["x"])
        if r.a:
            cmp(cen, P, f"Variação · {nome} · % (com sinal)", r.d / r.a * 100, s[3]["t"].replace("—", "").strip(), s[3]["x"], kind="pct_sinal")
        else:
            cmp_txt(cen, P, f"Variação · {nome} · %", "novo", s[3]["t"])


# ════════════════════════════════════════════════════════════════════
# 5. Cenários e execução no navegador
# ════════════════════════════════════════════════════════════════════
# Modo padrão: 61 cenários escolhidos (todos os períodos na Visão geral, demais páginas em recortes).
# Modo --completo: TODOS os 35 períodos × 8 opções de grupo (280 recortes) em TODAS as páginas,
#   nos 4 filtros da tabela e 24 comparações. Roda em processos paralelos (--processos=N, padrão 3)
#   e com as animações desligadas (prefers-reduced-motion); os testes funcionais (T1) mostram que a
#   animação não altera nenhum número.
import subprocess, traceback
from playwright.sync_api import sync_playwright

COMPLETO = "--completo" in sys.argv
PARTE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--parte=")), None)
NPROC = int(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--processos=")), "3"))

GRUPOS = list(DATA["grupos"].keys())
JANELAS = ([("mes", y, m) for y in (2025, 2026) for m in range(1, 13)] +
           [("tri", y, m) for y in (2025, 2026) for m in (1, 4, 7, 10)] +
           [("ano", 2025, 1), ("ano", 2026, 1), ("tudo", 2026, 1)])
if COMPLETO:
    RECORTES = [(j, g) for j in JANELAS for g in [""] + GRUPOS]
    HUB = TABELA = PAGS = RECORTES
    COMPARA = [(A_, B_, g) for g in [""] + GRUPOS for A_, B_ in [((20250101, 20250630), (20250701, 20251231)),
                                                                 ((20260101, 20260630), (20260701, 20261231)),
                                                                 ((20250101, 20251231), (20260101, 20261231))]]
else:
    HUB = [(j, "") for j in JANELAS] + [(("ano", 2025, 1), g) for g in GRUPOS] + [(("ano", 2026, 1), g) for g in GRUPOS] + \
          [(("mes", 2026, 8), g) for g in GRUPOS]
    PAGS = [(("mes", 2026, 8), ""), (("mes", 2025, 3), ""), (("mes", 2026, 1), ""), (("tri", 2026, 7), ""), (("tri", 2025, 10), ""),
            (("ano", 2025, 1), ""), (("ano", 2026, 1), ""), (("tudo", 2026, 1), ""), (("ano", 2025, 1), "MR"), (("ano", 2026, 1), "PE"),
            (("mes", 2026, 7), "TR")]
    TABELA = [(("mes", 2026, 8), ""), (("ano", 2025, 1), ""), (("tudo", 2026, 1), "MR")]
    COMPARA = [((20260101, 20260630), (20260701, 20261231), ""), ((20250101, 20250630), (20250701, 20251231), ""),
               ((20250101, 20251231), (20260101, 20261231), "TR")]


def nome_cen(j, g):
    p, y, m = j
    base = {"mes": f"{MES[m - 1]}/{y}", "tri": f"T{(m - 1) // 3 + 1}/{y}", "ano": str(y), "tudo": "base inteira"}[p]
    return base + (f" · grupo {DATA['grupos'][g]}" if g else "")


ESPERA = 350 if COMPLETO else 900


def settle(pg, minimo=None):
    pg.wait_for_timeout(ESPERA if minimo is None else minimo)
    prev = None
    for _ in range(30):
        cur = pg.inner_text(".pgx")
        if cur == prev:
            return
        prev = cur
        pg.wait_for_timeout(150)


def ir(pg, pgname, j, g):
    p, y, m = j
    qs = f"?pg={pgname}&preset={p}&ano={y}&mes={m}" + (f"&grupo={g}" if g else "")
    pg.evaluate("q=>{history.pushState(null,'',q);dispatchEvent(new PopStateEvent('popstate'));}", qs)
    settle(pg)


def seguro(cen, pagina, fn, *args):
    """executa uma conferência; se algo inesperado acontecer, registra como ERRO em vez de parar."""
    try:
        fn(cen, *args)
    except Exception as e:
        rows.append(dict(cenario=cen, pagina=pagina, metrica="(erro na conferência)", esperado=None, valor_exato_painel=None,
                         diferenca=None, tolerancia=None, exibido="", esperado_formatado="", texto_identico=None, status="ERRO",
                         nota=f"{type(e).__name__}: {e}"[:300]))


def executar(itens_rec, itens_cmp):
    """itens_rec: lista de (j, g, quais) com quais ⊆ {hub, tabela, paginas}."""
    erros = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROMIUM) if CHROMIUM else pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1500, "height": 1000}, reduced_motion="reduce" if COMPLETO else "no-preference")
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: erros.append(str(e)))
        pg.route("**/fonts.g*/**", lambda r: r.abort())
        pg.goto("file://" + HTML)
        pg.wait_for_selector(".pgx")
        settle(pg, 1500)
        global SUB
        for j, g, quais in itens_rec:
            lo, hi = janela(*j); SUB = recorte(lo, hi, g); an = anterior(*j)
            k = resumo(SUB); kp = resumo(recorte(*an, g)) if an else None
            cen = nome_cen(j, g)
            if "hub" in quais or "tabela" in quais:
                ir(pg, "hub", j, g)
            if "hub" in quais:
                seguro(cen, "Visão geral", lambda c, s: conf_hub(c, s, k, kp), pg.evaluate(SNAP_JS))
            if "tabela" in quais:
                for f in ["Em aberto", "Atrasados", "Quitados", "Todos"]:
                    pg.click(f".tools .seg button:has-text('{f}')"); settle(pg, 250 if COMPLETO else 500)
                    seguro(cen, "Tabela de títulos", lambda c, s, f=f: conf_tabela(c, s, k, f), pg.evaluate(SNAP_JS))
            if "paginas" in quais:
                labels, key = buckets(j[0], j[1], j[2], lo, hi)
                for nomep, rot, fn in [("fluxo", "Fluxo", lambda c, s: conf_fluxo(c, s, k, kp)),
                                       ("cap", "A pagar", lambda c, s: conf_cap(c, s, k, kp)),
                                       ("forn", "Fornecedores", lambda c, s: conf_forn(c, s, k, kp)),
                                       ("desp", "Categorias", lambda c, s: conf_desp(c, s, k, kp, labels, key)),
                                       ("perf", "Performance", lambda c, s: conf_perf(c, s, k, kp, labels, key))]:
                    ir(pg, nomep, j, g)
                    seguro(cen, rot, fn, pg.evaluate(SNAP_JS))
        for A_, B_, g in itens_cmp:
            ir(pg, "cmp", ("mes", 2026, 8), g)
            ins = pg.query_selector_all(".cmpbar input[type=date]")
            for el, v in zip(ins, [A_[0], A_[1], B_[0], B_[1]]):
                v = str(v); el.fill(f"{v[:4]}-{v[4:6]}-{v[6:]}")
            settle(pg, 400 if COMPLETO else 700)
            cen = f"A {dt(A_[0]):%d/%m/%Y}–{dt(A_[1]):%d/%m/%Y} × B {dt(B_[0]):%d/%m/%Y}–{dt(B_[1]):%d/%m/%Y}" + (f" · grupo {DATA['grupos'][g]}" if g else "")
            seguro(cen, "Comparar", lambda c, s: conf_cmp(c, s, A_, B_, g), pg.evaluate(SNAP_JS))
        b.close()
    return erros


def plano():
    """junta HUB, TABELA e PAGS num único roteiro por recorte (evita navegar duas vezes para o mesmo lugar)."""
    ordem, quais = [], {}
    for lista, q in [(HUB, "hub"), (TABELA, "tabela"), (PAGS, "paginas")]:
        for j, g in lista:
            if (j, g) not in quais:
                quais[(j, g)] = set(); ordem.append((j, g))
            quais[(j, g)].add(q)
    return [(j, g, quais[(j, g)]) for j, g in ordem]


SUB = None
t0 = time.time()
ITENS = plano()
if COMPLETO and PARTE:                       # processo filho: roda só a sua fatia e grava parcial
    kk, nn = map(int, PARTE.split("/"))
    erros = executar(ITENS[kk::nn], COMPARA[kk::nn])
    os.makedirs(f"{OUT}/partes", exist_ok=True)
    pd.DataFrame(rows).to_csv(f"{OUT}/partes/parte_{kk}.csv", index=False, encoding="utf-8-sig")
    json.dump(erros, open(f"{OUT}/partes/erros_js_{kk}.json", "w", encoding="utf-8"))
    sys.exit(0)
elif COMPLETO:                               # processo principal: dispara as fatias e junta
    procs = [subprocess.Popen([sys.executable, os.path.abspath(__file__), HTML, OUT, "--completo", f"--parte={i}/{NPROC}"])
             for i in range(NPROC)]
    codes = [p.wait() for p in procs]
    if any(codes):
        print("aviso: algum processo terminou com erro:", codes)
    partes = [pd.read_csv(f"{OUT}/partes/parte_{i}.csv") for i in range(NPROC) if os.path.exists(f"{OUT}/partes/parte_{i}.csv")]
    rows = pd.concat(partes, ignore_index=True).to_dict("records")
    erros_console = sum((json.load(open(f"{OUT}/partes/erros_js_{i}.json")) for i in range(NPROC)
                         if os.path.exists(f"{OUT}/partes/erros_js_{i}.json")), [])
else:
    erros_console = executar(ITENS, COMPARA)

R = pd.DataFrame(rows)
R.to_csv(f"{OUT}/reconciliacao.csv", index=False, encoding="utf-8-sig")
I = pd.DataFrame(integ)
res = {
    "arquivo": os.path.basename(HTML),
    "modo": "completo" if COMPLETO else "padrão",
    "titulos": len(df), "fornecedores": len(DATA["fornecedores"]),
    "cenarios": int(R.cenario.nunique()), "conferencias": len(R),
    "ok": int((R.status == "OK").sum()),
    "tolerancia": {"reais": TOL_REAIS, "percentual_relativa": TOL_REL, "contagens": 0},
    "conferidas_com_valor_exato": int(R.valor_exato_painel.notna().sum()),
    "texto_identico": int((R.texto_identico == True).sum()),
    "maior_diferenca_reais": float(R[R.tolerancia == TOL_REAIS].diferenca.abs().max()), "divergencias": int((R.status == "DIVERGE").sum()), "erros_leitura": int((R.status == "ERRO").sum()),
    "por_pagina": R.groupby("pagina").status.value_counts().unstack(fill_value=0).to_dict("index"),
    "integridade": I.status.value_counts().to_dict(),
    "erros_js": erros_console, "segundos": round(time.time() - t0, 1),
}
json.dump(res, open(f"{OUT}/resumo.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print(f"Integridade: {I.status.value_counts().to_dict()}")
for r in integ:
    if r["status"] != "OK":
        print(f"  [{r['status']}] {r['checagem']}: {r['detalhe']}")
print(f"Reconciliação ({res['modo']}): {res['conferencias']} números em {res['cenarios']} cenários · OK {res['ok']} · "
      f"divergências {res['divergencias']} · erros de leitura {res['erros_leitura']} · {res['segundos']}s")
if res["divergencias"] or res["erros_leitura"]:
    print(R[R.status != "OK"].groupby(["pagina", "metrica"]).size().sort_values(ascending=False).head(40).to_string())
