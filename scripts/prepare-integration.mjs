import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const engine = process.cwd()
const { writeCliFixtureInputs } = await import(pathToFileURL(path.join(engine, 'test/helpers/cli-fixture-inputs.mjs')))
const fixture = path.join(engine, 'temp/python-fixture')
fs.mkdirSync(fixture, { recursive: true })
await writeCliFixtureInputs(fixture)
const runtime = path.join(engine, 'release', `VIGO Studio-${process.platform}-${process.arch}`)
fs.appendFileSync(process.env.GITHUB_ENV, `VIGO_TEST_INPUTS=${fixture}\nVIGO_RUNTIME=${runtime}\n`)
