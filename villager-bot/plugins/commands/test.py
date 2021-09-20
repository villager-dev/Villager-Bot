import lightbulb
import hikari

print("imported")


class TestPlugin(lightbulb.Plugin):
    @lightbulb.command(name="test")
    async def test_command(self, ctx):
        await ctx.respond("Test!")
        await ctx.respond(embed=hikari.Embed(title="Test"))


def load(bot):
    print("loaded")
    bot.add_plugin(TestPlugin())


def unload(bot):
    bot.remove_plugin("TestPlugin")
