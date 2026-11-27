# ROADMAP.md — Motor de Otimização de Anúncios (Watcher + Patch)

## Status Geral do Projeto
- **Progresso atual:** **15 de 23 itens concluídos (~65%)**
- **Suíte de testes atual:** **46 testes automatizados passando (`python manage.py test`)**
- **Trava de segurança ativa:** **`MARKETPLACE_WRITE_ENABLED=False`** (modo somente leitura até a validação da Fase 3 no Mercado Livre real)
- **Bloqueio externo em aberto:** **Item 3** (aguardando aprovação da OpenPlatform no Mercado Livre Developers para liberar OAuth2 na conta PJ e concluir o **Item 14**)

---

## Checklist Completo das 6 Fases

### Fase 0: Fundação e Contexto (2/3)
- [x] **1.** Criar repositório e escrever o `CONTEXTO_SISTEMA.md` (arquitetura modular, regras invioláveis da WeDrop e padrões de código)
- [x] **2.** Estruturar projeto Django + PostgreSQL + Celery/Redis (`queue_fast` e `queue_media`)
- [ ] **3.** Criar aplicativo no Mercado Livre Developers e concluir OAuth2 *(em espera: formulário enviado para a OpenPlatform)*

### Fase 1: Cofre OAuth e Watcher — Mercado Livre (4/4)
- [x] **4.** Implementar `apps.accounts_vault` com criptografia Fernet, refresh automático de tokens, teto diário de patches e circuit-breaker
- [x] **5.** Criar interface `MarketplaceAdapter` e `MercadoLivreAdapter` (somente leitura)
- [x] **6.** Modelar `Listing` e `ListingSnapshot` imutável em `apps.catalog` (protegido contra edição/exclusão)
- [x] **7.** Criar `WatcherService` e task no Celery Beat (a cada 10 min) + comando `run_watcher` e `CSRF_TRUSTED_ORIGINS`

### Fase 2: Sub-Agentes de IA e Governança (3/3)
- [x] **8.** Criar `apps.governance` (`PromptConfig` com fallback por canal, blocklist `forbidden_terms`, `DailyQuotaLog` atômico e `seed_default_prompts`)
- [x] **9.** Implementar schemas Pydantic v2 e os 4 sub-agentes com `google-genai` (`gemini-2.5-flash`, `api_version="v1beta"`): Extrator Multimodal → Atributos → SEO/Copy → Auditor (com trava determinística em Python)
- [x] **10.** Modelar `OptimizationProposal`, task `optimize_listing_task` na `queue_fast` e comandos `seed_mock_wedrop_listings` e `optimize_listing`

### Fase 3: Revisão Humana e Patch Seguro (3/4)
- [x] **11.** Construir painel web Dark Mode de revisão Antes vs Depois (`/dashboard/` e `/dashboard/proposals/<id>/`, BEM CSS, paleta `#0b0f19`/`#182030`/`#3b82f6`/`#f97316`)
- [x] **12.** Implementar `sanitize_patch_payload()` com whitelist estrita WeDrop, `patch_content()` real no `MercadoLivreAdapter`, Pre-Flight Check (`sold_quantity > 0` omite `title`) e simulação Dry-Run
- [x] **13.** Implementar Rollback em 1 clique para `ListingSnapshot` baseline e `publish_proposal_task` com jitter (`45s–150s`) e circuit-breaker
- [ ] **14.** Validar ponta a ponta em 3 a 5 anúncios reais no Mercado Livre e monitorar sincronização de estoque da WeDrop por 24h *(depende do Item 3)*

### Fase 4: Estúdio Local de Mídia (3/5)
- [x] **15.** Criar `apps.media_studio` (`MediaAsset`) roteado estritamente na fila `queue_media` e contabilização `DailyQuotaLog.record_media_job()`
- [x] **16.** Implementar capa fundo branco `#FFFFFF` 1200x1200 local com `rembg` (modelo leve `u2netp` em CPU) e `Pillow` + recorte transparente PNG
- [x] **17.** Criar templates HTML/CSS de cards informativos (`card_benefits.html` e `card_specs.html`) renderizados em 1200x1200 via `Playwright` Chromium headless + comando `generate_media` (com suporte a `--source-image` arquivo/pasta e `--use-sample-image`)
- [ ] **18.** Construir pipeline de vídeo curto com `FFmpeg`, TTS PT-BR e legendas em 9:16 (ML Clips/TikTok) e 1:1 (Shopee)
- [ ] **19.** Conectar upload de imagens/vídeos no `MercadoLivreAdapter` e preview completo com player `<video>` no painel

### Fase 5: Multicanal e Produção (0/4)
- [ ] **20.** Cadastrar apps na Shopee Open Platform (v2) e TikTok Shop Seller API + suporte HMAC-SHA256 no `accounts_vault`
- [ ] **21.** Implementar `ShopeeAdapter` (com Media Space) e `TikTokAdapter` no padrão `MarketplaceAdapter`
- [ ] **22.** Configurar prompts específicos por canal e ligar auto-aprovação apenas para propostas com nota do Auditor >= 95 e zero alertas
- [ ] **23.** Subir em produção na VPS Ubuntu (Nginx, Gunicorn, Celery `queue_fast`/`queue_media`, Celery Beat, PM2, backup PostgreSQL e alertas)

---

## Decisões Técnicas Consolidadas & Comandos Úteis

### Decisões Técnicas Críticas Validadas
1. **Google GenAI SDK (`google-genai`):** Instanciação padronizada em `GeminiStructuredClient` com `http_options=types.HttpOptions(api_version="v1beta")`, `response_mime_type="application/json"` e `response_schema` Pydantic v2, vinculada ao orçamento diário `DailyQuotaLog`.
2. **Economia de RAM no `rembg` (`u2netp`):** O `rembg 2.0.88` usa por padrão `bria-rmbg-2.0` (`1.02 GB`, exigindo >800 MB por buffer de camada). O projeto fixa uma sessão explícita e em cache com `REMBG_MODEL_NAME="u2netp"` (`4.57 MB`) em `CPUExecutionProvider`, garantindo execução rápida sem estouro de memória tanto no Windows quanto na VPS Linux.
3. **Blindagem Estrita Anti-Quebra da WeDrop:** Dupla camada de proteção — os sub-agentes filtram `SELLER_SKU` e atributos `read_only`/`hidden` na entrada e na saída, e `sanitize_patch_payload()` lança `ForbiddenPayloadFieldError` caso `price`, `available_quantity`, `stock`, `variations`, `seller_custom_field` ou `seller_sku` apareçam no payload.
4. **Pre-Flight Check & Rollback Imutável:** `ListingSnapshot` bloqueia `save()` de atualização e `delete()` direto no model; antes de qualquer `patch_content()` ou rollback, `sold_quantity > 0` remove automaticamente o campo `title` do payload no Mercado Livre.
5. **Isolamento da `queue_media`:** Tarefas pesadas (`rembg`, `Playwright`, e futuramente `FFmpeg`) rodam exclusivamente na `queue_media` com concorrência reduzida (`--concurrency=1`).

### Principais Comandos de Gerenciamento (Windows PowerShell)
```powershell
# 1. Validar conexão somente leitura com conta real do Mercado Livre
.\venv\Scripts\python.exe manage.py test_ml_connection --account-id=1 --limit=5

# 2. Executar o Watcher manualmente de forma síncrona
.\venv\Scripts\python.exe manage.py run_watcher --account-id=1 --limit=5

# 3. Popular/atualizar os 4 prompts padrão e blocklists (idempotente)
.\venv\Scripts\python.exe manage.py seed_default_prompts

# 4. Popular conta sandbox e 2 anúncios mockados no padrão WeDrop (idempotente)
.\venv\Scripts\python.exe manage.py seed_mock_wedrop_listings

# 5. Executar o pipeline dos 4 sub-agentes (Gemini 2.5 Flash) em um anúncio
.\venv\Scripts\python.exe manage.py optimize_listing --external-id=MLB9000000001

# 6. Gerar Capa Fundo Branco 1200x1200 + 2 Cards HTML/Playwright (com foto/pasta local ou sample)
.\venv\Scripts\python.exe manage.py generate_media --proposal-id=1 --source-image="C:\caminho\para\foto_ou_pasta"
.\venv\Scripts\python.exe manage.py generate_media --proposal-id=1 --use-sample-image

# 7. Executar toda a suíte de testes automatizados (46 testes)
.\venv\Scripts\python.exe manage.py test
```
