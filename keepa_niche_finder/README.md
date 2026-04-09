# FBA SOURCING SYSTEM -- README
================================================================

## PIPELINE OVERVIEW

  Skill 1  Keepa Product Finder    1000 products per niche    ~20 mins
  Skill 2  SellerAmp Enrichment    200-400 products           ~1-2 hours
  Skill 3  Scoring + Shortlist     50-100 products            ~5 mins
  Skill 4  Supplier Research       full shortlist             ~1-2 hours

  Total per niche: ~3-5 hours
  5 niches in parallel: same wall clock time as 1 niche

================================================================
## FOLDER STRUCTURE

  ./                            (project root — wherever you cloned/installed this)
    CLAUDE.md                   Credentials + full project context
    LEAD-AGENT-PROMPTS.md       Paste into Claude Code per niche
    SUPPLIERS.CSV               Existing supplier accounts + logins
    README.md                   This file
    skills/
      skill-1-keepa-finder/     Phase 1 skill
      skill-2-selleramp/        Phase 2 skill
      skill-3-scoring/          Phase 3 skill
      skill-5-build-output/     Phase 5 skill
    config/
      niche-configs/
        afro-hair.md
        kids-toys.md
        educational-toys.md
        stationery.md
        sports-goods.md
    data/
      {niche}/                  All output files per niche
        outreach/               Brand approach emails

================================================================
## BEFORE FIRST RUN

  [ ] Add Keepa email + password to CLAUDE.md
  [ ] Add SellerAmp email + password to CLAUDE.md
  [ ] Replace example rows in SUPPLIERS.CSV with real accounts
  [ ] Install Claude Code: npm install -g @anthropic-ai/claude-code
  [ ] Enable agent teams in ~/.claude/settings.json:
        { "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
  [ ] Confirm Claude Max plan is active
  [ ] Confirm Keepa is logged in at keepa.com
  [ ] Confirm SellerAmp is logged in at sas.selleramp.com

================================================================
## RUNNING

  cd /path/to/keepa_niche_finder
  claude
  /model opus

  Paste the relevant niche prompt from LEAD-AGENT-PROMPTS.md.
  Run one niche first to confirm the pipeline works end to end.
  Then open 4 more terminals and run all 5 simultaneously.

================================================================
## CURRENT STATUS

  afro-hair:        Not started
  kids-toys:        Phase 1 done -- start from Phase 2
  educational-toys: Not started
  stationery:       Not started
  sports-goods:     Not started

================================================================
## ADDING SUPPLIERS

  Edit SUPPLIERS.CSV directly.
  Columns: NAME | Amazon Category | Website | Email | Password | Account Status
  Set Account Status to ACTIVE for accounts the agent should use.
  Set to INACTIVE to pause an account without deleting it.

================================================================
## WHEN SP-API IS APPROVED

  Add credentials to CLAUDE.md.
  Skill 2 will switch fee source to getMyFeesEstimateForASIN.
  Skill 2 will switch gating to the Listings Restrictions API.
  No other changes needed.
