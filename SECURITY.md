# Security Policy

## Secrets

This repository must not contain real Dify, DeepSeek, knowledge-base, or external-search API keys.

- Store `DIFY_KNOWLEDGE_API_KEY` as a **Secret** environment variable in Dify.
- Configure model credentials in Dify's model-provider settings.
- Do not paste credentials into prompts, Code nodes, DSL files, issues, screenshots, or test fixtures.
- Revoke and rotate a credential immediately if it is committed or otherwise exposed.

## Sensitive documents

The workflow may process contracts, personal information, employment files, and due-diligence materials. Before production use, configure access control, retention and deletion rules, encryption, audit logging, and an approved model/data-processing region. Do not use the public demo for confidential documents.

## Legal safety

The output is an internal review aid, not legal advice. High-risk findings and any legal basis marked `需人工复核` require confirmation by qualified counsel against the complete source document and current law.

## Reporting

If you find a secret or sensitive document in the repository, remove public access if possible, revoke the affected credential, and notify the repository owner privately. Do not publish the secret in a GitHub issue.

