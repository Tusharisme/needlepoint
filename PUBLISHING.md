# Publishing checklist

1. Replace the placeholders: `Tushar` (marketplace.json, plugin.json, LICENSE ×2) and `Tusharisme` (plugin.json, README.md).
   `grep -rn "YOUR_" .`
2. Create a public GitHub repo named `needlepoint` and push this folder as its root.
3. Validate:
   ```
   claude plugin validate .
   claude plugin validate ./plugins/needlepoint --profile directory
   python3 -m unittest discover -s tests
   ```
4. Test the install loop locally:
   ```
   claude plugin marketplace add ./
   claude plugin install needlepoint@needlepoint
   ```
   Start a session in any repo, then try: `$NDL doctor`, `$NDL find "..."`, and a Read of a big file (you should get an outline).
5. Run the end-to-end A/B and put the numbers in the README: `python3 bench/ab_claude_code.py /tmp/pystd --runs 3`.
6. Distribute:
   - Your own marketplace: users run `/plugin marketplace add Tusharisme/needlepoint`.
   - Anthropic's plugin directory: submit through the developer portal (the `--profile directory` validation above must pass).
   - Skill registries (skills.sh, tessl, etc.): they pick up `plugins/needlepoint/skills/needlepoint/SKILL.md` through `.claude-plugin/marketplace.json`.
7. When releasing a new version, bump `version` in plugin.json and add an entry to CHANGELOG.md.
