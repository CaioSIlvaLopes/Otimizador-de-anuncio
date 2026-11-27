Fase 0: Fundação e Contexto (2/3)

[x] 1. Criar repositório e escrever o CONTEXTO_SISTEMA.md — Arquitetura modular, regras invioláveis da WeDrop (nunca alterar SKU, estoque, preço ou variações) e padrões de código.

[x] 2. Estruturar projeto Django + PostgreSQL + Celery/Redis — .env, apps separados por domínio e duas filas isoladas (queue_fast e queue_media).

[ ] 3. Criar aplicativo no Mercado Livre Developers (aguardando OpenPlatform) — Gerar Client ID, Client Secret e conectar o fluxo OAuth2.

Fase 1: Cofre OAuth e Watcher (ML) (4/4)

[x] 4. Implementar accounts_vault com criptografia Fernet — MarketplaceAccount com refresh automático de tokens, teto diário de patches e circuit-breaker.

[x] 5. Criar MarketplaceAdapter e MercadoLivreAdapter (somente leitura) — Listagem de anúncios recentes, detalhes completos e atributos obrigatórios da categoria.

[x] 6. Modelar Listing e ListingSnapshot imutável em catalog — Snapshot do JSON original travado contra edição/exclusão para garantir rollback.

[x] 7. Criar task do Watcher no Celery Beat (a cada 10 min) — Captura de anúncios novos da WeDrop, atualização de sold_quantity e idempotência.

Fase 2: Sub-Agentes de IA e Governança (3/3)

[x] 8. Criar app governance (PromptConfig, blocklist e DailyQuotaLog) — Ajuste de prompts, palavras proibidas e teto diário de tokens no Django Admin.

[x] 9. Implementar schemas Pydantic e os 4 sub-agentes com Gemini 2.5 Flash — Fluxo: Extrator Multimodal → Ficha Técnica → SEO/Copy → Auditor (nota 0 a 100 + trava determinística).

[x] 10. Modelar OptimizationProposal e rodar esteira na queue_fast — Extrator travado para retornar null em especificações não comprovadas na fonte.

Fase 3: Revisão Humana e Patch Seguro (0/4 — próxima etapa)

[ ] 11. Construir painel web de revisão Antes vs Depois — Snapshot original lado a lado com a proposta, alertas do Auditor e ações Aprovar, Editar, Dry-Run ou Rejeitar.

[ ] 12. Implementar patch_content() com whitelist estrita e pre-flight check — Enviar apenas título, descrição e atributos; omitir título automaticamente se sold_quantity > 0.

[ ] 13. Implementar Rollback em 1 clique e Circuit-Breaker com jitter — Restaurar ListingSnapshot sob demanda, pausar conta após 3 erros seguidos e aplicar atraso aleatório entre envios.

[ ] 14. Validar ponta a ponta em 3 a 5 anúncios reais no Mercado Livre — Monitorar por 24h confirmando que a WeDrop continua sincronizando o estoque normalmente.

Fase 4: Estúdio Local de Mídia (0/5)

[ ] 15. Criar app media_studio isolado na fila queue_media — Limitar concorrência para 1 ou 2 workers para CPU/RAM não derrubarem a VPS.

[ ] 16. Implementar capa fundo branco local com rembg e Pillow — Recortar o produto real sem deformar detalhes e centralizar em canvas 1200x1200.

[ ] 17. Criar templates HTML/CSS de cards informativos via Playwright — Renderizar infográficos nítidos com medidas e benefícios validados, sem erros de texto.

[ ] 18. Construir pipeline de vídeo curto com FFmpeg, TTS e legendas — Gerar MP4 automático em 9:16 (ML Clips e TikTok) e 1:1 (Shopee) usando as fotos tratadas.

[ ] 19. Conectar upload de imagens/vídeos no MercadoLivreAdapter e preview no painel — Visualizar cards e vídeo final antes de publicar no portal.

Fase 5: Multicanal e Produção (0/4)

[ ] 20. Cadastrar apps na Shopee Open Platform (v2) e TikTok Shop Seller API — Adicionar suporte à assinatura HMAC-SHA256 no accounts_vault.

[ ] 21. Implementar ShopeeAdapter e TikTokAdapter no padrão de conectores — Upload prévio para o Media Space da Shopee e agrupamento de updates no TikTok Shop.

[ ] 22. Configurar prompts por canal e ligar auto-aprovação por nota — Publicar no automático apenas propostas com nota do Auditor ≥ 95 e zero alertas.

[ ] 23. Subir em produção na VPS (Nginx, Gunicorn, Celery e alertas) — Processos em background no PM2, backup do PostgreSQL e notificação para circuit-breaker ou fila humana.