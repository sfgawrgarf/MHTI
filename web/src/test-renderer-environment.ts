// Vue component acceptance tests use the client template transform and a custom renderer.
// https://vitest.dev/guide/environment#custom-environment
export default {
  name: 'vue-custom-renderer',
  viteEnvironment: 'client',
  setup() { return { teardown() {} } },
}
