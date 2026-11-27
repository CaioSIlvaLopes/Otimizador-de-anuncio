# CONTEXTO_SISTEMA.md — Motor de Otimização de Anúncios (Watcher + Patch)

## 1. Visão Geral e Objetivo
Sistema self-hosted em **Python + Django + PostgreSQL + Celery/Redis** para enriquecer e diferenciar automaticamente anúncios de dropshipping nacional criados pela integração da **WeDrop** nos marketplaces (**Mercado Livre**, **Shopee** e **TikTok Shop**).

O sistema opera no padrão **Watcher + Patch**:
1. A WeDrop cria os anúncios normalmente nos marketplaces e gerencia SKU, preço, estoque e pedidos.
2. O nosso sistema detecta anúncios recém-criados via API dos marketplaces, salva um snapshot imutável do estado original e executa uma esteira de sub-agentes de IA (**Gemini 2.5 Flash** com Structured Outputs) + estúdio local de mídia.
3. Após revisão humana (ou auto-aprovação por nota de confiança na Fase 5), o sistema envia `PUT/PATCH` diretamente aos portais atualizando **exclusivamente campos de conteúdo**.

---

## 2. Regras Invioláveis de Negócio (WeDrop & Blindagem de Contas)
Qualquer agente ou desenvolvedor alterando este repositório DEVE obedecer rigorosamente às regras abaixo:

1. **Proteção ao Vínculo da WeDrop (Whitelist Estrita):**
   * O sistema **JAMAIS** pode enviar, alterar ou sobrescrever os campos: **SKU** (`seller_custom_field`, `seller_sku`), **preço** (`price`), **estoque** (`available_quantity`, `stock`) ou **estrutura de variações** (`variations`).
   * Todo método `patch_content()` deve filtrar o payload por uma *whitelist* explícita de campos permitidos (`title`, `description`, `attributes`, `pictures`, `video`).
2. **Trava Global de Somente Leitura:**
   * A variável de ambiente `MARKETPLACE_WRITE_ENABLED` deve iniciar como `False`. Enquanto estiver `False`, nenhuma requisição `POST/PUT/PATCH/DELETE` de edição de anúncio pode ser disparada contra APIs externas (apenas `GET` e refresh de token OAuth).
3. **Snapshot Imutável Obrigatório:**
   * Nenhum anúncio pode ser modificado sem que exista previamente um registro em `ListingSnapshot` contendo o JSON bruto original (base para Rollback em 1 clique).
4. **Pre-Flight Check antes de Publicar:**
   * Antes de qualquer `PATCH`, consultar o anúncio ao vivo. No Mercado Livre, se `sold_quantity > 0`, remover automaticamente o campo `title` do payload para evitar erro de API.
5. **Anti-Alucinação Técnica:**
   * O Agente Extrator só pode registrar especificações comprovadas no texto/fotos da fonte. Qualquer dado ausente (voltagem, medidas, compatibilidade, garantia) deve ser retornado como `null` — nunca inventado.
6. **Segurança Operacional (Circuit-Breaker, Cotas e Pacing):**
   * Tokens OAuth e chaves de API ficam criptografados em repouso via **Fernet** (`cryptography`).
   * 3 erros consecutivos na API de uma loja acionam `is_paused_by_circuit_breaker = True` na conta.
   * Envios em lote devem respeitar `max_daily_patches` e aplicar atraso aleatório (*jitter*) entre requisições.

---

## 3. Stack Tecnológica
* **Linguagem & Framework:** Python 3.11+ / Django 5.x
* **Banco de Dados:** PostgreSQL
* **Filas e Agendamento:** Celery + Redis (Worker + Celery Beat)
* **Validação e IA:** Pydantic v2 + Google GenAI SDK (`gemini-2.5-flash` com JSON Schema)
* **Estúdio de Mídia (Local / Open-Source):** `rembg`, `Pillow`, `Playwright` (HTML/CSS -> PNG), `FFmpeg`, TTS (`edge-tts`)
* **Infraestrutura Alvo:** Desenvolvimento em Windows; produção em VPS Ubuntu Linux (Nginx + Gunicorn + PM2)

---

## 4. Arquitetura Modular de Pastas
Todos os apps Django residem dentro do diretório `apps/` para manter separação rígida por domínio:

```text
catalog_engine/
├── CONTEXTO_SISTEMA.md
├── manage.py
├── requirements.txt
├── .env.example
├── .gitignore
├── core/                      # Configurações Django (settings, urls, wsgi, asgi, celery.py)
├── apps/
│   ├── accounts_vault/        # Cofre de credenciais OAuth/HMAC (Fernet), cotas e circuit-breaker
│   ├── marketplaces/          # Padrão Adapter: base.py, mercadolivre.py, shopee.py, tiktok.py
│   ├── catalog/               # Listing, ListingSnapshot, OptimizationProposal, Rollback
│   ├── agents/                # Sub-agentes (Extrator, Ficha Técnica, SEO/Copy, Auditor) + Pydantic
│   ├── media_studio/          # rembg, Pillow, Playwright cards e pipeline FFmpeg
│   └── governance/            # PromptConfig, blocklists, DailyQuotaLog e painel de revisão
├── templates/
│   ├── dashboard/             # Telas web (Revisão Antes vs Depois)
│   └── media_cards/           # Templates HTML/CSS para infográficos
└── static/
```

---

## 5. Estratégia de Filas Celery
O processamento é estritamente isolado em **duas filas** para que tarefas pesadas de mídia não bloqueiem o monitoramento nem derrubem a VPS:
* **`queue_fast`:** Watcher (Celery Beat), chamadas de API dos marketplaces, execução dos sub-agentes de texto e envio de patches.
* **`queue_media`:** Remoção de fundo (`rembg`), renderização de cards (`Playwright`) e geração de vídeos (`FFmpeg`). Deve rodar com concorrência reduzida (`--concurrency=1` ou `2`):
  * **Windows (Desenvolvimento):** `celery -A core worker -Q queue_media --concurrency=1 --pool=solo -l info`
  * **Linux VPS (Produção):** `celery -A core worker -Q queue_media --concurrency=1 -l info`

---

## 6. Modelos Principais (Mapa de Dados)
* **`accounts_vault.MarketplaceAccount`:** loja conectada, canal, tokens criptografados, expiração, `max_daily_patches`, `consecutive_errors`, `is_paused_by_circuit_breaker`.
* **`catalog.Listing`:** vínculo com a conta, `external_id`, `sku_readonly`, `sold_quantity`, `status` (`DETECTED`, `PROCESSING`, `REVIEW`, `PUBLISHED`, `FAILED`), `confidence_score`.
* **`catalog.ListingSnapshot`:** `listing`, `raw_payload_json`, `captured_at` (imutável).
* **`catalog.OptimizationProposal`:** `listing`, `extracted_facts_json`, `proposed_title`, `proposed_description`, `proposed_attributes_json`, `media_assets_json`, `auditor_warnings`, `status`.
* **`governance.PromptConfig`:** `agent_name`, `channel`, `system_prompt`, `forbidden_terms`, `version`, `is_active`.
* **`governance.DailyQuotaLog`:** `date`, `llm_tokens_used`, `media_jobs_run`, `patches_sent_by_account`.

---

## 7. Diretrizes de Interface (Painel Web)
* **Tema:** Dark Mode focado em produtividade e leitura rápida (comparação lado a lado "Antes vs Depois").
* **Paleta Estrita:** Tipografia branca sobre fundos escuros neutros, usando **Azul (`#3b82f6`)** para ações primárias/navegação e **Laranja (`#f97316`)** para alertas, destaques e avisos do Auditor. **Não utilizar roxo ou verde** no design system.

---

## 8. Padrões de Desenvolvimento para Agentes (Antigravity)
1. Sempre ler este `CONTEXTO_SISTEMA.md` antes de planejar ou alterar código.
2. Trabalhar estritamente no escopo da tarefa atual — não antecipar fases futuras nem criar código morto.
3. Nunca hardcodar segredos, tokens ou chaves de API; usar `django-environ` ou `python-dotenv` via `.env`.
4. Todo código novo de regra crítica (whitelist, circuit-breaker, criptografia, validação Pydantic) deve incluir testes unitários básicos.
5. Manter docstrings e comentários curtos em português; nomes de arquivos, classes, variáveis e campos em inglês.