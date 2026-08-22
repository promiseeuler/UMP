# UMP Mintlify documentation

This directory is the publishing root for the Universal Machine Protocol
documentation site.

## Local preview

Mintlify requires Node.js 20.17 or newer.

```sh
cd mintlify
npx --yes mint@4.2.818 dev
```

Open `http://localhost:3000`.

## Validate before publishing

```sh
cd mintlify
npx --yes mint@4.2.818 validate
npx --yes mint@4.2.818 broken-links
npx --yes mint@4.2.818 a11y
```

The pinned CLI version makes local and CI checks reproducible. Review and
update the pin deliberately when Mintlify releases a required platform change.

## Publish with Mintlify

1. Create a Mintlify project and connect
   `https://github.com/promiseeuler/UMP`.
2. Set the documentation directory to `mintlify`.
3. Select the production branch.
4. Configure the desired Mintlify subdomain or custom domain.
5. Require `mint validate` and repository tests before merging documentation
   changes.
6. Trigger the first deployment and verify navigation, search, diagrams, dark
   mode, mobile layout, and outbound GitHub links.

Mintlify deploys subsequent commits from the configured branch automatically.

## Content rules

- Document behavior that exists in the reviewed source revision.
- Separate deterministic fixtures, live transport tests, simulation, and
  physical qualification claims.
- Use diagrams to clarify ownership, trust, lifecycle, or data flow.
- Include expected outcomes and failure behavior in procedural guides.
- Never describe UMP as actuator control or hardware certification.
- Keep command examples executable and use absolute deployment paths only as
  clearly labeled examples.
