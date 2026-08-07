from sqlalchemy import select

from app.models import Client, User
from app.seed import seed_all


async def test_seed_demo_data_true_creates_users_and_client(db_session):
    await seed_all(db_session, seed_demo_data=True)
    await db_session.commit()
    users = (await db_session.execute(select(User))).scalars().all()
    clients = (await db_session.execute(select(Client))).scalars().all()
    assert len(users) == 8
    assert len(clients) == 1


async def test_seed_demo_data_false_creates_no_users_or_client(db_session):
    # db_session fixture already calls seed_all(seed_demo_data=False) once;
    # calling it again with the same flag must still leave 0 users/clients.
    await seed_all(db_session, seed_demo_data=False)
    await db_session.commit()
    users = (await db_session.execute(select(User))).scalars().all()
    clients = (await db_session.execute(select(Client))).scalars().all()
    assert users == []
    assert clients == []
