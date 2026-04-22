# Sentinel — Van Ticket naar Code, Automatisch

## Management Samenvatting

Sentinel is een AI-gestuurd platform dat Jira-tickets automatisch omzet naar productieklare, beveiligde code. Waar een ontwikkelaar vandaag uren tot dagen besteedt aan het analyseren van een ticket, het schrijven van code, het uitvoeren van tests en het doorlopen van beveiligingscontroles, doet Sentinel dit autonoom — in een fractie van de tijd.

**Het resultaat:** ontwikkelteams leveren sneller, consistenter en veiliger op, terwijl zij zich richten op architectuur, besluitvorming en innovatie in plaats van routinematig implementatiewerk.

Sentinel is geen vervanging van de ontwikkelaar. Het is een versterking. Elke wijziging wordt voorgelegd aan een menselijke reviewer voordat deze in productie gaat. De ontwikkelaar verschuift van uitvoerder naar regisseur.

---

## Wat doet Sentinel?

### Het probleem

Softwareontwikkeling volgt een vast patroon: een ticket wordt aangemaakt, een ontwikkelaar analyseert de vereisten, schrijft code, test deze, laat een beveiligingscheck uitvoeren en dient een wijzigingsverzoek in. Dit is tijdrovend, foutgevoelig en moeilijk schaalbaar.

Organisaties worstelen met:

- **Lange doorlooptijden** — van ticket naar werkende code duurt dagen
- **Wisselende kwaliteit** — de ene ontwikkelaar werkt anders dan de andere
- **Beperkte capaciteit** — teams worden het knelpunt bij groei
- **Beveiligingsrisico's** — handmatige reviews missen kwetsbaarheden

### De oplossing

Sentinel automatiseert de implementatiefase door gespecialiseerde AI-agenten in te zetten die samenwerken in een gestructureerd proces:

1. **Analyse & Planning** — Een AI-agent leest het Jira-ticket, verkent de bestaande codebase en stelt een gedetailleerd implementatieplan op
2. **Ontwikkeling** — Een tweede agent schrijft de code volgens het plan, inclusief geautomatiseerde tests
3. **Beveiligingscontrole** — Een derde agent controleert alle wijzigingen op de OWASP Top 10 kwetsbaarheden en heeft vetorecht bij beveiligingsproblemen
4. **Iteratie** — Bij bevindingen worden wijzigingen automatisch aangepast en opnieuw gecontroleerd
5. **Oplevering** — Een merge request wordt aangemaakt in GitLab, klaar voor menselijke beoordeling

Dit alles gebeurt zonder menselijke tussenkomst tot het moment van review.

---

## Waarom is dit relevant voor uw organisatie?

### Snellere time-to-market

Implementatietijd per ticket daalt van dagen naar uren. Uw team kan meer werk verzetten zonder extra capaciteit in te hoeven schalen.

### Consistente kwaliteit

Elke wijziging doorloopt dezelfde gestandaardiseerde stappen — ongeacht wie het ticket heeft opgepakt. Patronen uit de bestaande codebase worden automatisch herkend en gevolgd.

### Ingebouwde beveiliging

Beveiligingscontrole is geen optionele stap meer, maar een verplicht onderdeel van elk traject. De beveiligingsagent heeft vetorecht: onveilige code wordt geblokkeerd voordat een mens het ziet.

### Schaalbaarheid zonder evenredige groei

Sentinel stelt uw huidige team in staat meer tickets parallel te verwerken. De noodzaak om bij groei lineair mee te schalen in ontwikkelcapaciteit neemt af.

### Behoud van controle

Sentinel levert geen code op in productie. Elke wijziging wordt als concept-merge-request aangeboden aan uw team. De mens blijft altijd de laatste poortwachter.

---

## Hoe verandert de rol van het team?

| Rol | Zonder Sentinel | Met Sentinel |
|-----|-----------------|--------------|
| **Ontwikkelaar** | Voornamelijk implementatie | Architectuur, review en besluitvorming |
| **Technisch Lead** | Hands-on coderen | Strategische keuzes en mentoring |
| **Security** | Achteraf controleren | Beoordeling van geautomatiseerde bevindingen |

Sentinel verschuift de focus van het team van *bouwen* naar *sturen*. Ontwikkelaars worden effectiever ingezet op werk dat menselijk oordeelsvermogen vereist.

---

## Waar werkt Sentinel mee samen?

Sentinel integreert naadloos met bestaande tooling:

- **Jira** — als bron van werk (tickets, vereisten, bijlagen)
- **GitLab** — als bestemming van resultaat (merge requests, discussies)
- **Docker** — voor geïsoleerde ontwikkelomgevingen per ticket

Er is geen wijziging nodig in uw bestaande ontwikkelproces. Sentinel sluit aan op de werkstroom die uw team al hanteert.

---

## Ondersteunde technologieën

Sentinel ondersteunt momenteel twee technologiestacks:

- **Drupal / PHP** — inclusief specifieke Drupal-beveiligingsreviews
- **Python** — met ondersteuning voor moderne frameworks

Uitbreiding naar aanvullende stacks (zoals React en Next.js) staat op de roadmap.

---

## Hoe ziet het proces eruit?

```
┌─────────────────┐
│   Jira-ticket    │  Medewerker maakt ticket aan
└────────┬────────┘
         ▼
┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  Plan-revisiecyclus                    ↑
│                                       │ │  Feedback?
  ┌─────────────────┐                  │
│ │  Sentinel Plan   │  AI analyseert   │ │
  └────────┬────────┘                  │
│          ▼                            │ │
  ┌─────────────────┐                  │
│ │ Draft Merge Req. │  Concept-MR      │ │
  └────────┬────────┘                  │
│          ▼                            │ │
  ┌─────────────────┐                  │
│ │  Plan Review     │  Ontwikkelaar ───┘ │
  └────────┬────────┘  valideert plan
└ ─ ─ ─ ─ ┼ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
           ▼
┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  Code-revisiecyclus                    ↑
│                                       │ │  Feedback?
  ┌─────────────────┐                  │
│ │ Sentinel Execute │  AI schrijft     │ │
  └────────┬────────┘  code & tests    │
│          ▼                            │ │
  ┌─────────────────┐                  │
│ │  Code Review     │  Ontwikkelaar ───┘ │
  └────────┬────────┘  beoordeelt code
└ ─ ─ ─ ─ ┼ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
           ▼
┌─────────────────┐
│   Acceptatie     │  Na goedkeuring: merge & deploy naar staging
└─────────────────┘
```

---

## Risico's en waarborgen

| Risico | Waarborg |
|--------|----------|
| AI genereert onveilige code | Beveiligingsagent met vetorecht op elke wijziging |
| Ongewenste wijzigingen in productie | Menselijke review is altijd vereist voor merge |
| Afwijking van codestandaarden | Agent herkent en volgt patronen uit bestaande codebase |
| Afhankelijkheid van AI-platform | Sentinel draait op bewezen Claude-modellen van Anthropic |

---

## Samengevat

Sentinel biedt uw organisatie de mogelijkheid om softwareontwikkeling te versnellen zonder concessies te doen aan kwaliteit of beveiliging. Het platform automatiseert het repetitieve implementatiewerk, terwijl uw team de regie behoudt over wat er in productie gaat.

**De kernbelofte:** meer output, hogere kwaliteit, snellere levering — met hetzelfde team.
