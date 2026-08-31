#include "AureliaDriveGameMode.h"

#include "AureliaCityBuilder.h"
#include "AureliaDriveHud.h"
#include "AureliaVehiclePawn.h"
#include "AureliaWorld.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/ExponentialHeightFogComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/ExponentialHeightFog.h"
#include "Engine/PostProcessVolume.h"
#include "Engine/SkyAtmosphere.h"
#include "Engine/SkyLight.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInstanceDynamic.h"

AAureliaDriveGameMode::AAureliaDriveGameMode()
{
	PrimaryActorTick.bCanEverTick = true;
	DefaultPawnClass = AAureliaVehiclePawn::StaticClass();
	HUDClass = AAureliaDriveHud::StaticClass();
}

void AAureliaDriveGameMode::BeginPlay()
{
	Super::BeginPlay();
	SpawnDusk();
	SpawnCity();
	SpawnGates();
	PlaceVehicle();
}

void AAureliaDriveGameMode::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (bFinished)
	{
		return;
	}

	APawn* Pawn = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Pawn || Gates.Num() == 0)
	{
		return;
	}

	const FVector Loc = Pawn->GetActorLocation();
	if (FVector::DistSquared2D(Loc, Gates[NextGate]) < FMath::Square(800.f))
	{
		AdvanceGate();
	}
}

FString AAureliaDriveGameMode::GetLapText() const
{
	if (bFinished)
	{
		return TEXT("DONE");
	}
	return FString::Printf(TEXT("LAP %d / %d"), Lap, AureliaWorld::LapCount);
}

FString AAureliaDriveGameMode::GetStatusText() const
{
	if (bFinished)
	{
		return TEXT("FINISHED — ORIGINAL CITY, NOT GTA 6");
	}
	return FString::Printf(TEXT("GATE %d / %d   PASS THE GOLD GATES"), NextGate + 1, AureliaWorld::GateCount);
}

void AAureliaDriveGameMode::SpawnDusk()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	FActorSpawnParameters Spawn;
	Spawn.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

	ADirectionalLight* Sun = World->SpawnActor<ADirectionalLight>(FVector(0.f, 0.f, 800.f), FRotator(-6.5f, 195.f, 0.f), Spawn);
	if (Sun)
	{
		if (UDirectionalLightComponent* Light = Cast<UDirectionalLightComponent>(Sun->GetLightComponent()))
		{
			Light->SetIntensity(12.f);
			Light->SetLightColor(FLinearColor(1.f, 0.72f, 0.48f));
			Light->SetAtmosphereSunLight(true);
			Light->SetCastShadows(true);
			Light->bUseRayTracedDistanceFieldShadows = true;
		}
	}

	ASkyLight* SkyLight = World->SpawnActor<ASkyLight>(FVector::ZeroVector, FRotator::ZeroRotator, Spawn);
	if (SkyLight)
	{
		if (USkyLightComponent* Sky = SkyLight->GetLightComponent())
		{
			Sky->SetIntensity(1.15f);
			Sky->SetRealTimeCapture(true);
		}
	}

	World->SpawnActor<ASkyAtmosphere>(FVector::ZeroVector, FRotator::ZeroRotator, Spawn);

	AExponentialHeightFog* Fog = World->SpawnActor<AExponentialHeightFog>(FVector(0.f, 0.f, 200.f), FRotator::ZeroRotator, Spawn);
	if (Fog)
	{
		if (UExponentialHeightFogComponent* FogComp = Fog->GetComponent())
		{
			FogComp->SetFogDensity(0.018f);
			FogComp->SetFogHeightFalloff(0.12f);
			FogComp->SetFogInscatteringColor(FLinearColor(0.55f, 0.28f, 0.22f));
		}
	}

	APostProcessVolume* Volume = World->SpawnActor<APostProcessVolume>(FVector::ZeroVector, FRotator::ZeroRotator, Spawn);
	if (Volume)
	{
		Volume->bUnbound = true;
		Volume->BlendWeight = 1.f;
		Volume->Settings.bOverride_BloomIntensity = true;
		Volume->Settings.BloomIntensity = 0.85f;
		Volume->Settings.bOverride_AutoExposureBias = true;
		Volume->Settings.AutoExposureBias = 0.25f;
		Volume->Settings.bOverride_ColorSaturation = true;
		Volume->Settings.ColorSaturation = FVector4(1.08f, 1.02f, 0.96f, 1.f);
		Volume->Settings.bOverride_FilmGrainIntensity = true;
		Volume->Settings.FilmGrainIntensity = 0.12f;
	}

	if (APlayerController* PC = World->GetFirstPlayerController())
	{
		PC->bShowMouseCursor = false;
	}
}

void AAureliaDriveGameMode::SpawnCity()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	FActorSpawnParameters Spawn;
	Spawn.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
	AAureliaCityBuilder* City = World->SpawnActor<AAureliaCityBuilder>(FVector::ZeroVector, FRotator::ZeroRotator, Spawn);
	if (City)
	{
		City->Build();
	}
}

void AAureliaDriveGameMode::SpawnGates()
{
	using namespace AureliaWorld;
	const float R = Ring;
	Gates = {
		FVector(-R, -R, 80.f),
		FVector(0.f, -R, 80.f),
		FVector(R, -R, 80.f),
		FVector(R, 0.f, 80.f),
		FVector(R, R, 80.f),
		FVector(0.f, R, 80.f),
		FVector(-R, R, 80.f),
		FVector(-R, 0.f, 80.f),
	};

	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	UStaticMesh* Cube = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));
	UMaterialInterface* Shape = LoadObject<UMaterialInterface>(nullptr, TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
	if (!Cube)
	{
		return;
	}

	FActorSpawnParameters Spawn;
	Spawn.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

	for (int32 Index = 0; Index < Gates.Num(); ++Index)
	{
		const FLinearColor Color = Index == 0 ? FLinearColor(1.f, 0.77f, 0.54f) : FLinearColor(0.49f, 0.94f, 1.f);
		UMaterialInstanceDynamic* Mid = Shape ? UMaterialInstanceDynamic::Create(Shape, this) : nullptr;
		if (Mid)
		{
			Mid->SetVectorParameterValue(TEXT("Color"), Color);
			Mid->SetVectorParameterValue(TEXT("BaseColor"), Color);
		}

		const FVector Base = Gates[Index];
		const FRotator Rot(0.f, ((Index >= 2 && Index < 4) || Index >= 6) ? 90.f : 0.f, 0.f);

		auto PlaceBar = [&](const FVector& Offset, const FVector& Scale)
		{
			AStaticMeshActor* Bar = World->SpawnActor<AStaticMeshActor>(Base + Offset, Rot, Spawn);
			if (!Bar)
			{
				return;
			}
			UStaticMeshComponent* Comp = Bar->GetStaticMeshComponent();
			Comp->SetMobility(EComponentMobility::Movable);
			Comp->SetStaticMesh(Cube);
			if (Mid)
			{
				Comp->SetMaterial(0, Mid);
			}
			Bar->SetActorScale3D(Scale);
			Comp->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		};

		PlaceBar(FVector(0.f, 0.f, 320.f), FVector(8.5f, 0.18f, 0.18f));
		PlaceBar(FVector(-415.f, 0.f, 160.f), FVector(0.18f, 0.18f, 3.2f));
		PlaceBar(FVector(415.f, 0.f, 160.f), FVector(0.18f, 0.18f, 3.2f));
	}
}

void AAureliaDriveGameMode::PlaceVehicle()
{
	APawn* Pawn = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Pawn)
	{
		return;
	}

	const FVector Start(-AureliaWorld::Ring, -AureliaWorld::Ring - 1800.f, 80.f);
	const FRotator Facing(0.f, 90.f, 0.f);
	Pawn->SetActorLocationAndRotation(Start, Facing, false, nullptr, ETeleportType::ResetPhysics);
	if (AAureliaVehiclePawn* Car = Cast<AAureliaVehiclePawn>(Pawn))
	{
		Car->CaptureSpawnPoint();
	}
}

void AAureliaDriveGameMode::AdvanceGate()
{
	++NextGate;
	if (NextGate >= Gates.Num())
	{
		NextGate = 0;
		if (Lap >= AureliaWorld::LapCount)
		{
			bFinished = true;
			return;
		}
		++Lap;
	}
}
