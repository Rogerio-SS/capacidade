"""
Servidor do Dashboard de Capacidade — armazenamento em PostgreSQL.

Variáveis de ambiente necessárias:
    DATABASE_URL   URL de conexão PostgreSQL (ex: postgresql://user:pass@host/db)
    PORTA          Porta HTTP (padrão 5000)

Como rodar localmente:
    pip install -r requirements.txt
    set DATABASE_URL=postgresql://...
    python server.py

Deploy gratuito recomendado: Render (web) + Supabase (banco).
"""

import json
import os
import sys
import threading
from datetime import datetime

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request, send_file
from glob import glob

# ----------------------------------------------------------------------
# Configuração
# ----------------------------------------------------------------------
PASTA = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_HTML = "Capacidade_empresa_v14.html"

DATABASE_URL = os.environ.get("DATABASE_URL", "")
PORTA = int(os.environ.get("PORTA") or (sys.argv[1] if len(sys.argv) > 1 else 5000))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024

_trava = threading.Lock()


# ----------------------------------------------------------------------
# Banco de dados
# ----------------------------------------------------------------------
def _conectar():
    url = DATABASE_URL
    # Render/Heroku entregam URLs com "postgres://" mas psycopg2 quer "postgresql://"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)


def inicializar_banco():
    """Cria a tabela e a linha inicial se ainda não existirem."""
    with _conectar() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS estado (
                    id         INTEGER PRIMARY KEY DEFAULT 1,
                    versao     INTEGER NOT NULL DEFAULT 0,
                    dados      JSONB,
                    atualizado_em  TEXT,
                    atualizado_por TEXT,
                    CHECK (id = 1)
                );
            """)
            cur.execute("""
                INSERT INTO estado (id, versao, dados, atualizado_em, atualizado_por)
                VALUES (1, 0, NULL, NULL, NULL)
                ON CONFLICT (id) DO NOTHING;
            """)
        conn.commit()


def ler_estado():
    try:
        with _conectar() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT versao, dados, atualizado_em, atualizado_por FROM estado WHERE id = 1"
                )
                row = cur.fetchone()
        if row is None:
            return {"versao": 0, "dados": None, "atualizadoEm": None, "atualizadoPor": None}
        return {
            "versao": row["versao"],
            "dados": row["dados"],
            "atualizadoEm": row["atualizado_em"],
            "atualizadoPor": row["atualizado_por"],
        }
    except Exception as erro:
        print(f"  [AVISO] Falha ao ler banco: {erro}")
        return {"versao": 0, "dados": None, "atualizadoEm": None, "atualizadoPor": None}


def gravar_estado(versao, dados, atualizado_em, atualizado_por):
    with _conectar() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE estado
                SET versao = %s,
                    dados  = %s,
                    atualizado_em  = %s,
                    atualizado_por = %s
                WHERE id = 1
            """, (versao, json.dumps(dados, ensure_ascii=False), atualizado_em, atualizado_por))
        conn.commit()


# ----------------------------------------------------------------------
# Rotas
# ----------------------------------------------------------------------
@app.route("/")
def pagina():
    caminho = os.path.join(PASTA, ARQUIVO_HTML)
    if not os.path.exists(caminho):
        encontrados = sorted(glob(os.path.join(PASTA, "Capacidade_empresa_v*.html")))
        if encontrados:
            caminho = encontrados[-1]
        else:
            return (
                f"<h2>Arquivo não encontrado</h2>"
                f"<p>Coloque <code>{ARQUIVO_HTML}</code> na mesma pasta do "
                f"<code>server.py</code>.</p>",
                404,
            )
    resposta = send_file(caminho)
    resposta.headers["Cache-Control"] = "no-store, must-revalidate"
    return resposta


@app.route("/api/data", methods=["GET"])
def obter_dados():
    estado = ler_estado()
    return jsonify({
        "ok": True,
        "versao": estado["versao"],
        "dados": estado["dados"],
        "atualizadoEm": estado["atualizadoEm"],
        "atualizadoPor": estado["atualizadoPor"],
    })


@app.route("/api/versao", methods=["GET"])
def obter_versao():
    estado = ler_estado()
    return jsonify({
        "versao": estado["versao"],
        "atualizadoEm": estado["atualizadoEm"],
        "atualizadoPor": estado["atualizadoPor"],
    })


@app.route("/api/data", methods=["POST"])
def salvar_dados():
    corpo = request.get_json(silent=True)
    if not isinstance(corpo, dict) or "dados" not in corpo:
        return jsonify({"ok": False, "erro": "Corpo inválido: falta 'dados'."}), 400

    with _trava:
        estado = ler_estado()
        versao_cliente = corpo.get("versao", 0)
        forcar = bool(corpo.get("forcar"))

        if not forcar and estado["dados"] is not None and versao_cliente != estado["versao"]:
            return jsonify({
                "ok": False,
                "conflito": True,
                "versaoServidor": estado["versao"],
                "versaoEnviada": versao_cliente,
                "atualizadoEm": estado["atualizadoEm"],
                "atualizadoPor": estado["atualizadoPor"],
            }), 409

        nova_versao = estado["versao"] + 1
        agora = datetime.now().isoformat(timespec="seconds")
        usuario = corpo.get("usuario") or "desconhecido"

        try:
            gravar_estado(nova_versao, corpo["dados"], agora, usuario)
        except Exception as erro:
            print(f"  [ERRO] Falha ao gravar: {erro}")
            return jsonify({"ok": False, "erro": str(erro)}), 500

    return jsonify({
        "ok": True,
        "versao": nova_versao,
        "atualizadoEm": agora,
        "atualizadoPor": usuario,
    })


@app.route("/api/limpar", methods=["POST"])
def limpar_dados():
    with _trava:
        estado = ler_estado()
        nova_versao = estado["versao"] + 1
        agora = datetime.now().isoformat(timespec="seconds")
        usuario = (request.get_json(silent=True) or {}).get("usuario", "desconhecido")
        gravar_estado(nova_versao, None, agora, usuario)
    return jsonify({"ok": True, "versao": nova_versao})


@app.route("/api/status", methods=["GET"])
def status():
    estado = ler_estado()
    return jsonify({
        "ok": True,
        "banco": "PostgreSQL",
        "versao": estado["versao"],
        "atualizadoEm": estado["atualizadoEm"],
        "atualizadoPor": estado["atualizadoPor"],
    })


# ----------------------------------------------------------------------
# Inicialização
# ----------------------------------------------------------------------
def main():
    if not DATABASE_URL:
        print("  [ERRO] Variável DATABASE_URL não definida.")
        print("  Defina: set DATABASE_URL=postgresql://usuario:senha@host/banco")
        sys.exit(1)

    print("=" * 64)
    print("  Dashboard de Capacidade - servidor (PostgreSQL)")
    print("=" * 64)
    print(f"  Banco: {DATABASE_URL[:40]}...")
    print("  Inicializando tabela...")
    inicializar_banco()
    estado = ler_estado()
    print(f"  Versão atual: {estado['versao']}"
          + (f" (por {estado['atualizadoPor']} em {estado['atualizadoEm']})"
             if estado["atualizadoPor"] else "  (sem dados ainda)"))
    sufixo = "" if PORTA == 80 else f":{PORTA}"
    print("-" * 64)
    print(f"  Acesse: http://localhost{sufixo}")
    print("  Para parar: Ctrl+C")
    print("=" * 64)

    try:
        from waitress import serve
        print("  Servidor: waitress\n")
        serve(app, host="0.0.0.0", port=PORTA, threads=8)
    except ImportError:
        print("  Servidor: Flask embutido\n")
        app.run(host="0.0.0.0", port=PORTA, debug=False)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Servidor encerrado.")
        sys.exit(0)
