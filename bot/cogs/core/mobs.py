import asyncio
import itertools
import math
import random
import typing

import classyjson as cj
import discord
from datetime import datetime, timezone
from discord.ext import commands

from bot.cogs.core.database import Database
from bot.cogs.core.quests import Quests
from bot.utils.ctx import Ctx
from bot.utils.misc import SuppressCtxManager, emojify_item, make_health_bar
from bot.villager_bot import VillagerBotCluster


class MobSpawner(commands.Cog):
    def __init__(self, bot: VillagerBotCluster):
        self.bot = bot

        self.d = bot.d
        self.karen = bot.karen

        self.active_channels = set[int]()

    @property
    def db(self) -> Database:
        return typing.cast(Database, self.bot.get_cog("Database"))

    @property
    def quests(self) -> Quests:
        return typing.cast("Quests", self.bot.get_cog("Quests"))

    def engage_check(self, ctx: Ctx):
        def _engage_check(m: discord.Message):
            if m.channel != ctx.channel:
                return False

            if ctx.author.bot:
                return False

            if m.content.lower().lstrip(ctx.prefix) not in self.d.mobs_mech.valid_attacks:
                return False

            return ctx.author.id not in self.bot.botban_cache

        return _engage_check

    def attack_check(self, ctx: Ctx, engage_msg):
        def _attack_check(m):
            if not (m.channel == engage_msg.channel and m.author == engage_msg.author):
                return False

            return (
                m.content.lower().lstrip(ctx.prefix.lower()) in self.d.mobs_mech.valid_attacks
                or m.content.lower().lstrip(ctx.prefix.lower()) in self.d.mobs_mech.valid_flees
            )

        return _attack_check

    async def calculate_sword_damage(self, user_id: int, sword: str, multi: float) -> int:
        if sword == "Netherite Sword":
            damage = random.randint(7, 10)
        elif sword == "Diamond Sword":
            damage = random.randint(6, 7)
        elif sword == "Gold Sword":
            damage = random.randint(4, 5)
        elif sword == "Iron Sword":
            damage = random.randint(2, 4)
        elif sword == "Stone Sword":
            damage = random.randint(1, 3)
        elif sword == "Wood Sword":
            damage = random.randint(1, 2)
        else:
            raise ValueError(f"{repr(sword)} is not a valid sword.")

        if await self.db.fetch_item(user_id, "Sharpness II Book") is not None:
            damage *= 1.5
        elif await self.db.fetch_item(user_id, "Sharpness I Book") is not None:
            damage *= 1.25

        if multi > 1:
            damage /= 1.3

        return math.ceil(damage)

    async def spawn_event(self, ctx: Ctx):
        if ctx.channel.id in self.active_channels:
            return

        self.active_channels.add(ctx.channel.id)

        try:
            await self._spawn_event(ctx)
        except Exception:
            await self.bot.get_cog("Events").on_error("mob_spawn", ctx)
        finally:
            self.active_channels.remove(ctx.channel.id)

    async def _spawn_event(self, ctx: Ctx):
        if ctx.guild is None:  # ignore dms
            return

        # The difficulty of the mob spawned is based off who triggered the action, rather
        # than the person who actually engages the mob
        ctx_user_pickaxe_lvl = len(self.d.mining.pickaxes) - self.d.mining.pickaxes.index(
            await self.db.fetch_pickaxe(ctx.author.id)
        )
        difficulty: typing.Literal["easy", "hard"]
        if ctx_user_pickaxe_lvl >= 3:  # Gold pick
            difficulty = "hard"
        else:
            difficulty = "easy"

        difficulty_multi = {"easy": 1, "hard": 1.5}[difficulty]

        # type of mob to be spawned
        mob_key = random.choice(tuple(self.d.mobs_mech.mobs))
        mob = cj.classify(
            {**self.d.mobs_mech.mobs[mob_key].dict(), **ctx.l.mobs_mech.mobs[mob_key].dict()},
        )
        mob_max_health = mob.health

        await asyncio.sleep(random.random() * 3)

        # engage embed
        embed = discord.Embed(
            color=self.bot.embed_color,
            title=f"**{random.choice(ctx.l.mobs_mech.mob_drops).format(mob.nice.lower())}**",
            description=ctx.l.mobs_mech.type_engage,
        )
        embed.set_image(url=mob.image)

        engage_msg = await ctx.send(embed=embed)

        # get the user who is going to be attacking the mob
        while True:
            try:
                initial_attack_msg: discord.Message = await self.bot.wait_for(
                    "message",
                    check=self.engage_check(ctx),
                    timeout=15,
                )
            except asyncio.TimeoutError:
                await engage_msg.edit(suppress=True)
                return

            user = initial_attack_msg.author

            if await self.karen.check_econ_paused(user.id):
                continue

            db_user = await self.db.fetch_user(user.id)
            user_health = db_user.health

            if user_health < 1:
                await ctx.send_embed(ctx.l.mobs_mech.no_health)
                continue

            break

        # fetch user's sword, slime trophy, and suppress the engage message
        user_sword, slime_trophy, _ = await asyncio.gather(
            self.db.fetch_sword(user.id),
            self.db.fetch_item(user.id, "Slime Trophy"),
            engage_msg.edit(suppress=True),
        )

        await self.karen.econ_pause(user.id)

        try:
            for iteration in itertools.count(start=1):
                # create embed with mob image
                embed = discord.Embed(
                    color=self.bot.embed_color,
                    title=ctx.l.mobs_mech.attack_or_flee,
                )
                embed.set_image(url=mob.image)

                # add user health bar to embed
                embed.add_field(
                    name=f"**{user.display_name}**",
                    value=make_health_bar(
                        user_health,
                        20,
                        self.d.emojis.heart_full,
                        self.d.emojis.heart_half,
                        self.d.emojis.heart_empty,
                    ),
                    inline=False,
                )

                # add mob health bar to embed
                embed.add_field(
                    name=f"**{mob.nice}**",
                    value=make_health_bar(
                        mob.health,
                        mob_max_health,
                        self.d.emojis.heart_full,
                        self.d.emojis.heart_half,
                        self.d.emojis.heart_empty,
                    ),
                    inline=False,
                )

                fight_msg = await ctx.send(embed=embed)
                user_action: str | None = None

                # listen for attack or flee message (or timeout)
                while user_action not in self.d.mobs_mech.valid_attacks:
                    try:
                        user_action_msg: discord.Message = await self.bot.wait_for(
                            "message",
                            check=self.attack_check(ctx, initial_attack_msg),
                            timeout=30,
                        )
                        user_action = user_action_msg.content.lstrip(ctx.prefix).lower()
                    except asyncio.TimeoutError:
                        timed_out = True
                    else:
                        timed_out = False

                    # check if user is a fucking baby
                    if timed_out or user_action in self.d.mobs_mech.valid_flees:
                        await fight_msg.edit(suppress=True)
                        await ctx.send_embed(random.choice(ctx.l.mobs_mech.flee_insults))

                        return

                user_dmg = await self.calculate_sword_damage(user.id, user_sword, difficulty_multi)

                # user attack miss logic
                if mob_key == "baby_slime":
                    if iteration < 3 and slime_trophy is None:
                        user_dmg = 0
                    elif slime_trophy is not None and random.choice((True, False, False)):
                        user_dmg = 0
                    elif iteration >= 3 and random.choice((True, False)):
                        user_dmg = 0
                elif mob_key == "enderman":  # noqa: SIM102
                    if iteration >= 1 and random.choice((False, False, False, False, False, True)):
                        user_dmg = 0

                mob.health -= user_dmg

                if mob.health < 1:  # user wins
                    await fight_msg.edit(suppress=True)
                    await ctx.send_embed(
                        random.choice(ctx.l.mobs_mech.user_finishers).format(
                            mob.nice.lower(),
                            user_sword.lower(),
                        ),
                    )

                    break
                if user_dmg == 0:  # if user missed
                    await ctx.send_embed(random.choice(mob.misses).format(user_sword.lower()))
                else:  # send regular attack message
                    await ctx.send_embed(
                        random.choice(ctx.l.mobs_mech.user_attacks).format(
                            mob.nice.lower(),
                            user_sword.lower(),
                        ),
                    )

                async with SuppressCtxManager(ctx.typing()):
                    await asyncio.sleep(0.75 + random.random() * 2)

                mob_dmg = random.randint(2, 6)

                if mob_key == "creeper":  # add creeper mechanics
                    if iteration > 2 and random.choice((True, True, False)):
                        # creeper yeets your bloodied corpse across the map
                        user_health = 0

                        await fight_msg.edit(suppress=True)
                        await ctx.send_embed(random.choice(mob.finishers))

                        break

                    mob_dmg = 0

                user_health -= mob_dmg
                user_health = max(user_health, 0)

                if user_health < 1:  # send mob finisher
                    await ctx.send_embed(random.choice(mob.finishers))
                    break
                # send mob attack
                mob_attack_text = random.choice(mob.attacks)
                if "{}" in mob_attack_text:
                    mob_attack_text = mob_attack_text.format(datetime.now(tz=timezone.utc).year)

                await ctx.send_embed(mob_attack_text)

                async with SuppressCtxManager(ctx.typing()):
                    await asyncio.sleep(0.75 + random.random() * 2)

                await fight_msg.edit(suppress=True)

            # outside of the for loop
            embed = discord.Embed(color=self.bot.embed_color)
            embed.set_image(url=mob.image)

            embed.add_field(  # user health bar
                name=f"**{user.display_name}**",
                value=make_health_bar(
                    max(user_health, 0),
                    20,
                    self.d.emojis.heart_full,
                    self.d.emojis.heart_half,
                    self.d.emojis.heart_empty,
                ),
                inline=False,
            )

            embed.add_field(  # mob health bar
                name=f"**{mob.nice}**",
                value=make_health_bar(
                    max(mob.health, 0),
                    mob_max_health,
                    self.d.emojis.heart_full,
                    self.d.emojis.heart_half,
                    self.d.emojis.heart_empty,
                ),
                inline=False,
            )

            await ctx.send(embed=embed)

            db_user = await self.db.fetch_user(user.id)
            user_bal = db_user.emeralds

            # calculate looting level
            looting_level = 0
            if await self.db.fetch_item(user.id, "Looting II Book") is not None:
                looting_level = 2
            elif await self.db.fetch_item(user.id, "Looting I Book") is not None:
                looting_level = 1

            if user_health > 0:  # user win
                # if mob is slime, determine if it drops slime balls (1/26 chance)
                if mob_key == "baby_slime" and random.randint(0, 25 - (looting_level * 4)) == 1:
                    balls_won = random.randint(1, {"easy": 10, "hard": 20}[difficulty])

                    # increase balls won depending on looting level
                    balls_won = round(balls_won * ({0: 1, 1: 1.25, 2: 1.5}[looting_level]))

                    await self.db.add_item(user.id, "Slime Ball", 5, balls_won, True)

                    await ctx.send_embed(
                        random.choice(ctx.l.mobs_mech.found).format(
                            f"{balls_won}{self.d.emojis.slimeball}"
                        ),
                    )
                # if mob is skeleton determine if it should drop bone meal
                elif mob_key == "skeleton" and random.randint(0, 20 - (looting_level * 3)) == 1:
                    await self.db.add_item(user.id, "Bone Meal", 512, 1)

                    await ctx.send_embed(
                        random.choice(ctx.l.mobs_mech.found).format(f"1{self.d.emojis.bone_meal}"),
                    )
                # if mob is enderman determine if it should drop enderpearl
                elif mob_key == "enderman" and random.randint(0, 30 - (looting_level * 3)) == 1:
                    pearls_won = random.randint(1, {"easy": 1, "hard": 3}[difficulty])

                    await self.db.add_item(user.id, "Ender Pearl", 1024, pearls_won)

                    await ctx.send_embed(
                        random.choice(ctx.l.mobs_mech.found).format(
                            f"{pearls_won}{self.d.emojis.ender_pearl}"
                        ),
                    )
                # mob should just drop normal rewards
                else:
                    # calculate emeralds won based off difficulty
                    if difficulty == "easy":
                        ems_won = (
                            int(user_bal * (1 / random.choice((3, 3.25, 3.5, 3.75, 4))))
                            if user_bal < 256
                            else int(512 * (1 / random.choice((3, 3.25, 3.5, 3.75, 4))))
                        )
                    else:  # difficulty hard
                        ems_won = (
                            int(user_bal * (1 / random.choice((1.75, 2, 2.25, 2.5))))
                            if user_bal < 256
                            else int(512 * (1 / random.choice((1.75, 2, 2.25, 2.5))))
                        )

                    ems_won = int((ems_won if ems_won > 0 else 1) * difficulty_multi)
                    # increase ems won depending on looting_level
                    ems_won = int(ems_won * ({0: 1, 1: 1.25, 2: 1.75}[looting_level]))

                    reward_info: list[tuple[str, int]] = [
                        (self.d.emojis.emerald, ems_won),
                    ]

                    if random.randint(0, 40 - (looting_level * 3)) == 1:
                        reward_info.append((self.d.emojis.infernums_scroll, 1))
                        await self.db.add_item(user.id, "Infernum's Scroll", 512, 1, False, True)

                    await self.db.balance_add(user.id, ems_won)
                    await self.db.update_lb(user.id, "week_emeralds", 1, "add")

                    await ctx.send_embed(
                        random.choice(ctx.l.mobs_mech.found).format(
                            " + ".join([f"{amount}{emoji}" for emoji, amount in reward_info])
                        ),
                    )

                await self.db.update_lb(user.id, "mobs_killed", 1, "add")
                await self.quests.update_user_daily_quest(ctx, f"killed_{mob_key}", 1)
            else:  # mob win
                # determine how many emeralds they lose based off difficulty
                if difficulty == "easy":
                    ems_lost = (
                        int(user_bal * (1 / (random.choice([3.05, 3.3, 3.55, 3.8]) + 0.3)))
                        if user_bal > 20
                        else random.randint(2, 4)
                    )
                else:  # difficulty hard
                    ems_lost = (
                        int(user_bal * (1 / (random.choice([1.45, 1.55, 1.65, 1.75]) + 0.3)))
                        if user_bal > 20
                        else random.randint(5, 9)
                    )

                ems_lost = await self.db.balance_sub(user.id, ems_lost)

                if (
                    mob_key == "enderman"
                    and random.randint(1, {"easy": 10, "hard": 5}[difficulty]) == 1
                ):
                    stealable_items = [
                        item
                        for item in await self.db.fetch_items(user.id)
                        if item.amount > 1 and emojify_item(self.d, item.name) != self.d.emojis.air
                    ]

                    if len(stealable_items) > 2:
                        stolen_item = random.choice(stealable_items)

                        await self.db.remove_item(user.id, stolen_item.name, 1)

                        await ctx.send_embed(
                            random.choice(ctx.l.mobs_mech.lost.enderman).format(
                                1,
                                emojify_item(self.d, stolen_item.name),
                            ),
                        )

                        return

                if mob_key in {"creeper", "enderman"}:
                    await ctx.send_embed(
                        random.choice(ctx.l.mobs_mech.lost[mob_key]).format(
                            ems_lost,
                            self.d.emojis.emerald,
                        ),
                    )
                else:
                    await ctx.send_embed(
                        random.choice(ctx.l.mobs_mech.lost.normal).format(
                            mob.nice.lower(),
                            ems_lost,
                            self.d.emojis.emerald,
                        ),
                    )
        finally:
            await self.db.update_user(user.id, health=user_health)
            await self.karen.econ_unpause(user.id)


async def setup(bot: VillagerBotCluster) -> None:
    await bot.add_cog(MobSpawner(bot))
